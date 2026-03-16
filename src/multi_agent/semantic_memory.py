"""Semantic Memory — cross-task knowledge persistence with retrieval.

Stores structured memory entries (architectural decisions, code patterns,
conventions, preferences) and retrieves them via TF-IDF cosine similarity
or optional OpenAI embeddings.

Storage: ``.multi-agent/memory/semantic.jsonl``

Configuration (.ma.yaml):
    memory:
      backend: tfidf          # tfidf (default) or openai
      openai_model: text-embedding-3-small
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
_MAX_CONTENT_LENGTH = 2000
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
    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH]

    if category not in CATEGORIES:
        category = "general"

    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    entry_id = _content_hash(content)

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
        with path.open("a+", encoding="utf-8") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                # Read through same locked fd to prevent TOCTOU race
                f.seek(0)
                existing = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        existing.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

                for e in existing:
                    if e.get("id") == entry_id:
                        return {"status": "duplicate", "entry_id": entry_id, "count": len(existing)}

                if len(existing) >= _MAX_ENTRIES:
                    return {"status": "error", "reason": f"memory full ({_MAX_ENTRIES} entries)"}

                f.seek(0, 2)  # seek to end for append
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
        file_size = path.stat().st_size
        if file_size > _MAX_MEMORY_FILE_SIZE:
            _log.warning("Semantic memory file too large: %d bytes", file_size)
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


# ── OpenAI Embeddings Backend (optional) ─────────────────


def _get_memory_config() -> dict[str, Any]:
    """Load memory config from .ma.yaml memory: section."""
    try:
        from multi_agent.config import load_project_config
        proj = load_project_config()
        raw = proj.get("memory")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _get_backend() -> str:
    """Determine which search backend to use: 'openai' or 'tfidf'."""
    cfg = _get_memory_config()
    backend = cfg.get("backend", "tfidf")
    if backend == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "tfidf"


_EMBED_CACHE_FILE = "embeddings_cache.json"


def _embeddings_cache_path() -> Path:
    return _memory_dir() / _EMBED_CACHE_FILE


def _load_embeddings_cache() -> dict[str, list[float]]:
    """Load cached embeddings from disk. Key = content hash, value = vector."""
    path = _embeddings_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_embeddings_cache(cache: dict[str, list[float]]) -> None:
    """Save embeddings cache to disk."""
    path = _embeddings_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        _log.warning("Failed to save embeddings cache: %s", e)


_MAX_EMBED_CHARS = 8000  # conservative limit for embedding input text
_MAX_EMBED_CACHE_ENTRIES = 10000


def _openai_embed(texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
    """Call OpenAI embeddings API. Returns list of vectors."""
    import urllib.request

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    capped = [t[:_MAX_EMBED_CHARS] for t in texts]
    payload = json.dumps({"input": capped, "model": model}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return [item["embedding"] for item in body["data"]]


def _cosine_sim_vectors(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two dense vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _search_openai(
    query: str, entries: list[dict[str, Any]], top_k: int, min_score: float,
) -> list[dict[str, Any]]:
    """Search using OpenAI embeddings with caching."""
    cfg = _get_memory_config()
    model = cfg.get("openai_model", "text-embedding-3-small")

    cache = _load_embeddings_cache()
    texts_to_embed: list[str] = []
    idx_to_hash: dict[int, str] = {}

    # Prepare: find entries without cached embeddings
    entry_hashes: list[str] = []
    for e in entries:
        h = _content_hash(e.get("content", ""))
        entry_hashes.append(h)
        if h not in cache:
            texts_to_embed.append(e.get("content", ""))
            idx_to_hash[len(texts_to_embed) - 1] = h

    # Embed missing entries + query
    all_texts = texts_to_embed + [query]
    if all_texts:
        try:
            vectors = _openai_embed(all_texts, model=model)
        except Exception as e:
            _log.warning("OpenAI embeddings failed, falling back to TF-IDF: %s", e)
            return _search_tfidf(query, entries, top_k, min_score)

        # Cache new entry embeddings
        for i, h in idx_to_hash.items():
            cache[h] = vectors[i]
        # Prune cache if too large
        if len(cache) > _MAX_EMBED_CACHE_ENTRIES:
            keys = sorted(cache.keys())
            for k in keys[:len(cache) - _MAX_EMBED_CACHE_ENTRIES]:
                del cache[k]
        _save_embeddings_cache(cache)
        query_vec = vectors[-1]
    else:
        try:
            query_vec = _openai_embed([query], model=model)[0]
        except Exception as e:
            _log.warning("OpenAI query embed failed, falling back to TF-IDF: %s", e)
            return _search_tfidf(query, entries, top_k, min_score)

    # Score entries
    results: list[dict[str, Any]] = []
    for entry, h in zip(entries, entry_hashes):
        evec = cache.get(h)
        if evec is None:
            continue
        score = _cosine_sim_vectors(query_vec, evec)
        if score >= min_score:
            results.append({"entry": entry, "score": round(score, 4)})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


# ── Search / Retrieval ───────────────────────────────────


def _search_tfidf(
    query: str, entries: list[dict[str, Any]], top_k: int, min_score: float,
) -> list[dict[str, Any]]:
    """TF-IDF based search (default backend)."""
    doc_tokens = [_tokenize(e.get("content", "") + " " + " ".join(e.get("tags", []))) for e in entries]
    query_tokens = _tokenize(query)

    if not query_tokens:
        return []

    all_docs = doc_tokens + [query_tokens]
    idf = _build_idf(all_docs)
    query_vec = _tfidf_vector(query_tokens, idf)
    doc_vecs = [_tfidf_vector(dt, idf) for dt in doc_tokens]

    results: list[dict[str, Any]] = []
    for entry, doc_vec in zip(entries, doc_vecs):
        score = _cosine_similarity(query_vec, doc_vec)

        content_lower = entry.get("content", "").lower()
        query_lower = query.lower()
        if query_lower in content_lower:
            score += 0.3

        entry_tags = {t.lower() for t in entry.get("tags", [])}
        for qt in query_tokens:
            if qt in entry_tags:
                score += 0.1

        if score >= min_score:
            results.append({"entry": entry, "score": round(score, 4)})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def search(
    query: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    category: str | None = None,
    min_score: float = 0.05,
) -> list[dict[str, Any]]:
    """Search memory entries by semantic similarity.

    Uses OpenAI embeddings when configured (memory.backend=openai + OPENAI_API_KEY),
    otherwise falls back to TF-IDF cosine similarity.

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

    backend = _get_backend()
    if backend == "openai":
        return _search_openai(query, entries, top_k, min_score)
    return _search_tfidf(query, entries, top_k, min_score)


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
    """Rewrite all entries to disk (after delete/clear).

    Uses 'w' mode which atomically creates/truncates, then acquires lock
    before writing to prevent concurrent readers from seeing partial data.
    """
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


# ── Auto-Prune ───────────────────────────────────────────

_DEFAULT_TTL_DAYS = 180  # 6 months


def prune(
    *,
    max_age_days: int | None = None,
    max_entries: int | None = None,
) -> dict[str, Any]:
    """Prune old or excess memory entries.

    Args:
        max_age_days: Remove entries older than this (default: 180 days).
        max_entries: Keep only the N most recent entries (by timestamp).

    Returns:
        Dict with 'removed' count and 'remaining' count.
    """
    entries = _load_entries()
    if not entries:
        return {"removed": 0, "remaining": 0}

    original_count = len(entries)
    now = time.time()

    # Phase 1: TTL expiry
    ttl_days = max_age_days if max_age_days is not None else _DEFAULT_TTL_DAYS
    if ttl_days > 0:
        cutoff = now - (ttl_days * 86400)
        entries = [e for e in entries if e.get("ts", now) >= cutoff]

    # Phase 2: Cap total entries (keep most recent)
    cap = max_entries if max_entries is not None else _MAX_ENTRIES
    if len(entries) > cap:
        entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
        entries = entries[:cap]

    removed = original_count - len(entries)
    if removed > 0:
        _rewrite_entries(entries)

    return {"removed": removed, "remaining": len(entries)}


# ── Export / Import ───────────────────────────────────────


_MAX_IMPORT_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def export_entries(out_path: str) -> int:
    """Export all memory entries to a JSON file for sharing.

    Args:
        out_path: Output file path (JSON format).

    Returns:
        Number of entries exported.
    """
    entries = _load_entries()
    export_data = {
        "version": 1,
        "exported_at": time.time(),
        "count": len(entries),
        "entries": entries,
    }
    Path(out_path).write_text(
        json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(entries)


def import_entries(in_path: str) -> dict[str, Any]:
    """Import memory entries from a JSON file.

    Deduplicates by content hash — existing entries are skipped.

    Args:
        in_path: Input file path (JSON format, from export_entries).

    Returns:
        Dict with 'imported' and 'skipped' counts.
    """
    path = Path(in_path)
    if not path.exists():
        return {"imported": 0, "skipped": 0, "error": "file not found"}

    try:
        fsize = path.stat().st_size
    except OSError:
        return {"imported": 0, "skipped": 0, "error": "cannot stat file"}

    if fsize > _MAX_IMPORT_FILE_SIZE:
        return {"imported": 0, "skipped": 0, "error": f"file too large ({fsize} bytes)"}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"imported": 0, "skipped": 0, "error": str(e)}

    if not isinstance(data, dict) or "entries" not in data:
        return {"imported": 0, "skipped": 0, "error": "invalid format (missing 'entries')"}

    entries_to_import = data["entries"]
    if not isinstance(entries_to_import, list):
        return {"imported": 0, "skipped": 0, "error": "entries must be a list"}

    existing = _load_entries()
    existing_ids = {e.get("id") for e in existing}

    imported = 0
    skipped = 0
    for entry in entries_to_import:
        if not isinstance(entry, dict) or not entry.get("content"):
            skipped += 1
            continue
        eid = entry.get("id") or _content_hash(entry["content"])
        if eid in existing_ids:
            skipped += 1
            continue
        # Validate category
        if entry.get("category") not in CATEGORIES:
            entry["category"] = "general"
        # Cap content
        entry["content"] = str(entry["content"])[:_MAX_CONTENT_LENGTH]
        entry["id"] = eid
        existing.append(entry)
        existing_ids.add(eid)
        imported += 1

        if len(existing) >= _MAX_ENTRIES:
            break

    if imported > 0:
        _rewrite_entries(existing)

    return {"imported": imported, "skipped": skipped}


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
