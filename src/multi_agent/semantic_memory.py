"""Semantic Memory — cross-task knowledge persistence with retrieval.

Stores structured memory entries (architectural decisions, code patterns,
conventions, preferences) and retrieves them via TF-IDF cosine similarity.

Storage: ``.multi-agent/memory/semantic.jsonl``

Optionally supports OpenAI embeddings when OPENAI_API_KEY is set,
falling back to built-in TF-IDF for zero-dependency operation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from multi_agent.config import workspace_dir

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]
fcntl = _fcntl

_log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────

_MAX_MEMORY_FILE_SIZE = 20 * 1024 * 1024  # 20 MB cap
_MAX_ENTRIES = 5000
_DEFAULT_TOP_K = 5

# Memory entry categories
CATEGORIES = frozenset({
    "architecture",   # design decisions, patterns, tech stack
    "convention",     # naming, formatting, project-specific rules
    "pattern",        # code patterns, idioms, anti-patterns
    "bugfix",         # recurring bugs, root causes, fixes
    "preference",     # user/team preferences
    "context",        # project context, domain knowledge
    "general",        # uncategorized
})


# ── Storage ──────────────────────────────────────────────

def _memory_dir() -> Path:
    return workspace_dir() / "memory"


def _memory_file() -> Path:
    return _memory_dir() / "semantic.jsonl"


def _content_hash(content: str) -> str:
    """Generate a short hash of content for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def store(
    content: str,
    *,
    category: str = "general",
    source: str = "",
    task_id: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a memory entry.

    Args:
        content: The memory text (required, non-empty).
        category: One of CATEGORIES.
        source: Where this memory came from (e.g. "review", "user", "build").
        task_id: Associated task ID.
        tags: Optional list of keyword tags.
        metadata: Optional extra metadata dict.

    Returns:
        Dict with entry_id, status, and entry count.
    """
    content = content.strip()
    if not content:
        return {"status": "error", "reason": "empty content"}

    if category not in CATEGORIES:
        category = "general"

    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    entry_id = _content_hash(content)

    # Dedup check
    existing = _load_entries()
    for e in existing:
        if e.get("id") == entry_id:
            return {"status": "duplicate", "entry_id": entry_id, "count": len(existing)}

    if len(existing) >= _MAX_ENTRIES:
        return {"status": "error", "reason": f"memory full ({_MAX_ENTRIES} entries)"}

    entry = {
        "id": entry_id,
        "ts": time.time(),
        "content": content,
        "category": category,
        "source": source,
        "task_id": task_id,
        "tags": tags or [],
        "metadata": metadata or {},
    }

    try:
        with path.open("a", encoding="utf-8") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        _log.warning("Failed to write memory entry: %s", e)
        return {"status": "error", "reason": str(e)}

    return {"status": "stored", "entry_id": entry_id, "count": len(existing) + 1}


def _load_entries() -> list[dict[str, Any]]:
    """Load all memory entries from disk."""
    path = _memory_file()
    if not path.exists():
        return []
    try:
        if path.stat().st_size > _MAX_MEMORY_FILE_SIZE:
            _log.warning("Semantic memory file too large: %d bytes", path.stat().st_size)
            return []
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return entries


# ── TF-IDF Engine ────────────────────────────────────────

_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could and or but not no nor "
    "for to of in on at by from with as it its this that these those "
    "i me my we our you your he him his she her they them their "
    "what which who whom how when where why all each every some any "
    "if then else than so very just also only too much more most "
    "about after before between into through during up down out off over under".split()
)


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase terms, stripping stop words."""
    tokens = re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _build_idf(documents: list[list[str]]) -> dict[str, float]:
    """Compute IDF scores across a set of tokenized documents."""
    n = len(documents)
    if n == 0:
        return {}
    df: Counter[str] = Counter()
    for doc in documents:
        df.update(set(doc))
    return {term: math.log((n + 1) / (freq + 1)) + 1 for term, freq in df.items()}


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Compute TF-IDF vector for a single document."""
    tf = Counter(tokens)
    total = len(tokens) or 1
    return {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Compute cosine similarity between two sparse vectors."""
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Search / Retrieval ───────────────────────────────────

def search(
    query: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    category: str | None = None,
    min_score: float = 0.05,
) -> list[dict[str, Any]]:
    """Search memory entries by semantic similarity.

    Args:
        query: Natural language query.
        top_k: Maximum number of results.
        category: Optional filter by category.
        min_score: Minimum similarity score to include.

    Returns:
        List of dicts with 'entry' and 'score', sorted by score descending.
    """
    entries = _load_entries()
    if not entries:
        return []

    if category and category in CATEGORIES:
        entries = [e for e in entries if e.get("category") == category]

    if not entries:
        return []

    # Tokenize all entries and query
    doc_tokens = [_tokenize(e.get("content", "") + " " + " ".join(e.get("tags", []))) for e in entries]
    query_tokens = _tokenize(query)

    if not query_tokens:
        return []

    # Build IDF from corpus + query
    all_docs = doc_tokens + [query_tokens]
    idf = _build_idf(all_docs)

    # Compute TF-IDF vectors
    query_vec = _tfidf_vector(query_tokens, idf)
    doc_vecs = [_tfidf_vector(dt, idf) for dt in doc_tokens]

    # Score and rank
    results: list[dict[str, Any]] = []
    for i, (entry, doc_vec) in enumerate(zip(entries, doc_vecs)):
        score = _cosine_similarity(query_vec, doc_vec)

        # Boost: exact substring match
        content_lower = entry.get("content", "").lower()
        query_lower = query.lower()
        if query_lower in content_lower:
            score += 0.3

        # Boost: tag match
        entry_tags = {t.lower() for t in entry.get("tags", [])}
        for qt in query_tokens:
            if qt in entry_tags:
                score += 0.1

        if score >= min_score:
            results.append({"entry": entry, "score": round(score, 4)})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def get_context(
    query: str,
    *,
    top_k: int = 3,
    max_chars: int = 2000,
) -> str:
    """Get relevant memory context as a formatted string for LLM prompts.

    Args:
        query: The task/query to find relevant context for.
        top_k: Max entries to include.
        max_chars: Max total characters.

    Returns:
        Formatted string of relevant memories, or empty string.
    """
    results = search(query, top_k=top_k)
    if not results:
        return ""

    lines = ["## Relevant Project Memory\n"]
    total = 0
    for r in results:
        entry = r["entry"]
        cat = entry.get("category", "general")
        content = entry.get("content", "")
        line = f"- [{cat}] {content}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)

    return "\n".join(lines) + "\n"


# ── Management ───────────────────────────────────────────

def delete(entry_id: str) -> dict[str, Any]:
    """Delete a memory entry by ID."""
    entries = _load_entries()
    new_entries = [e for e in entries if e.get("id") != entry_id]
    if len(new_entries) == len(entries):
        return {"status": "not_found", "entry_id": entry_id}

    _rewrite_entries(new_entries)
    return {"status": "deleted", "entry_id": entry_id, "remaining": len(new_entries)}


def clear(*, category: str | None = None) -> dict[str, Any]:
    """Clear all memory entries, optionally filtered by category."""
    entries = _load_entries()
    if category:
        new_entries = [e for e in entries if e.get("category") != category]
        removed = len(entries) - len(new_entries)
        _rewrite_entries(new_entries)
        return {"status": "cleared", "category": category, "removed": removed, "remaining": len(new_entries)}

    path = _memory_file()
    if path.exists():
        path.unlink()
    return {"status": "cleared", "removed": len(entries)}


def list_entries(
    *,
    category: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List memory entries, optionally filtered by category."""
    entries = _load_entries()
    if category:
        entries = [e for e in entries if e.get("category") == category]
    return entries[-limit:]


def stats() -> dict[str, Any]:
    """Get memory statistics."""
    entries = _load_entries()
    by_cat: dict[str, int] = {}
    for e in entries:
        cat = e.get("category", "general")
        by_cat[cat] = by_cat.get(cat, 0) + 1

    return {
        "total_entries": len(entries),
        "by_category": by_cat,
        "file": str(_memory_file()),
        "file_exists": _memory_file().exists(),
    }


def _rewrite_entries(entries: list[dict[str, Any]]) -> None:
    """Rewrite all entries to disk (after delete/clear)."""
    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        _log.warning("Failed to rewrite memory: %s", e)


# ── Auto-capture helpers ─────────────────────────────────

def capture_from_review(
    task_id: str,
    review_summary: str,
    *,
    agent_id: str = "",
) -> dict[str, Any]:
    """Auto-capture architectural insights from a review summary.

    Extracts key decisions, patterns, and conventions mentioned in the
    review and stores them as separate memory entries.
    """
    if not review_summary or len(review_summary) < 20:
        return {"captured": 0}

    # Extract sentences that look like decisions/patterns
    indicators = [
        "should", "must", "always", "never", "prefer", "avoid",
        "convention", "pattern", "architecture", "standard",
        "important", "note", "remember", "ensure", "rule",
        "决定", "约定", "规范", "架构", "模式", "标准", "注意", "确保",
    ]

    sentences = re.split(r'[.。!\n]+', review_summary)
    captured = 0

    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 15 or len(sent) > 500:
            continue

        sent_lower = sent.lower()
        if any(ind in sent_lower for ind in indicators):
            category = "convention"
            if any(w in sent_lower for w in ("architecture", "design", "pattern", "架构", "设计", "模式")):
                category = "architecture"
            elif any(w in sent_lower for w in ("bug", "fix", "error", "issue", "修复", "错误")):
                category = "bugfix"

            result = store(
                sent,
                category=category,
                source=f"review:{agent_id}" if agent_id else "review",
                task_id=task_id,
                tags=["auto-captured", "review"],
            )
            if result.get("status") == "stored":
                captured += 1

    return {"captured": captured, "task_id": task_id}
