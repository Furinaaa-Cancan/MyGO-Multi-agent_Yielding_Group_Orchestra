#!/usr/bin/env python3
"""
SWE-bench Adapter — load, classify, and evaluate SWE-bench tasks.

Provides integration between the MyGO experiment framework and the
SWE-bench Verified benchmark. Handles:
1. Loading and caching the SWE-bench dataset
2. Complexity classification and stratified sampling
3. Instance setup (clone repo at correct commit)
4. Evaluation (apply gold test patch and run tests)

Usage:
    # List available tasks with complexity
    python scripts/swebench_adapter.py --list

    # Sample 30 tasks stratified by complexity
    python scripts/swebench_adapter.py --sample 30

    # Setup a specific instance
    python scripts/swebench_adapter.py --setup django__django-16379

    # Evaluate an instance
    python scripts/swebench_adapter.py --evaluate django__django-16379

Reference: experiment-protocol-v2.md §4.1
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CACHE_DIR = PROJECT_ROOT / ".multi-agent" / "swebench_cache"
INSTANCES_DIR = PROJECT_ROOT / ".multi-agent" / "swebench_instances"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

_log = logging.getLogger(__name__)


# ── Dataset Loading ──────────────────────────────────────


def _ensure_dataset_cached(split: str = "verified") -> Path:
    """Download and cache the SWE-bench dataset if not present.

    Uses the HuggingFace datasets library or falls back to direct download.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"swebench_{split}.json"

    if cache_file.exists():
        _log.info("Using cached dataset: %s", cache_file)
        return cache_file

    # Try HuggingFace datasets first
    try:
        from datasets import load_dataset

        dataset_name = "princeton-nlp/SWE-bench_Verified" if split == "verified" else "princeton-nlp/SWE-bench_Lite"
        ds = load_dataset(dataset_name, split="test")
        data = [dict(row) for row in ds]
        cache_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _log.info("Downloaded %d instances to %s", len(data), cache_file)
        return cache_file

    except ImportError:
        _log.warning("datasets library not installed. Install: pip install datasets")

    # Fallback: direct download from HuggingFace API
    try:
        import urllib.request

        url = (
            "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/main/data/test.jsonl"
            if split == "verified"
            else "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/resolve/main/data/test.jsonl"
        )
        _log.info("Downloading from %s ...", url)
        tmp_file = cache_file.with_suffix(".tmp")
        urllib.request.urlretrieve(url, str(tmp_file))

        # Parse JSONL
        data = []
        for line in tmp_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                data.append(json.loads(line))

        cache_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_file.unlink(missing_ok=True)
        _log.info("Downloaded %d instances to %s", len(data), cache_file)
        return cache_file

    except Exception as e:
        _log.error("Failed to download SWE-bench dataset: %s", e)
        raise RuntimeError(
            f"Cannot load SWE-bench dataset. Install 'datasets' library or check network.\n"
            f"  pip install datasets\n"
            f"Error: {e}"
        ) from e


def load_swebench_tasks(
    split: str = "verified",
    sample_size: int | None = None,
    seed: int = 42,
    complexity_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Load SWE-bench tasks with complexity classification.

    Args:
        split: "verified" or "lite"
        sample_size: If set, stratified sample of this size
        seed: Random seed for reproducibility
        complexity_filter: "simple", "medium", or "complex" to filter

    Returns:
        List of task dicts compatible with experiment_runner_v2.discover_tasks()
    """
    cache_file = _ensure_dataset_cached(split)
    raw_data = json.loads(cache_file.read_text(encoding="utf-8"))

    tasks = []
    for item in raw_data:
        instance_id = item.get("instance_id", "")
        # Extract gold patch stats for complexity classification
        patch = item.get("patch", "")
        test_patch = item.get("test_patch", "")

        complexity_info = _classify_swebench_instance(patch, item)

        # Build requirement from problem statement
        problem_statement = item.get("problem_statement", "")
        if not problem_statement:
            continue

        # Filter by language (Python only for now)
        repo = item.get("repo", "")
        if not _is_python_repo(repo):
            continue

        task = {
            "task_id": _sanitize_instance_id(instance_id),
            "task_source": "swebench",
            "instance_id": instance_id,
            "requirement": problem_statement,
            "has_ground_truth": bool(test_patch),
            "task_dir": str(INSTANCES_DIR / _sanitize_instance_id(instance_id)),
            "complexity": complexity_info["level"],
            "complexity_score": complexity_info["score"],
            "complexity_details": complexity_info,
            "repo": repo,
            "base_commit": item.get("base_commit", ""),
            "patch": patch,
            "test_patch": test_patch,
            "hints_text": item.get("hints_text", ""),
            "created_at": item.get("created_at", ""),
            "version": item.get("version", ""),
        }
        tasks.append(task)

    # Filter by complexity
    if complexity_filter:
        tasks = [t for t in tasks if t["complexity"] == complexity_filter]

    # Stratified sampling
    if sample_size and sample_size < len(tasks):
        tasks = _stratified_sample(tasks, sample_size, seed)

    _log.info(
        "Loaded %d SWE-bench tasks (split=%s, sample=%s)",
        len(tasks), split, sample_size,
    )
    return tasks


def _is_python_repo(repo: str) -> bool:
    """Check if a repo is primarily Python (based on known SWE-bench repos)."""
    # Most SWE-bench repos are Python. Exclude known non-Python.
    non_python = {"vuejs/vue", "facebook/react", "expressjs/express"}
    return repo.lower() not in non_python


def _sanitize_instance_id(instance_id: str) -> str:
    """Convert SWE-bench instance_id to safe task_id."""
    import re
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", instance_id).strip("-").lower()
    if len(safe) > 63:
        h = hashlib.sha256(instance_id.encode()).hexdigest()[:8]
        safe = safe[:54] + "-" + h
    return safe


# ── Complexity Classification ────────────────────────────


def _classify_swebench_instance(patch: str, item: dict) -> dict[str, Any]:
    """Classify SWE-bench instance complexity from gold patch.

    Uses patch statistics as oracle complexity signal:
    - files_changed: number of files in the diff
    - lines_changed: total additions + deletions
    - cross_module: whether changes span multiple packages
    """
    if not patch:
        return {"level": "medium", "score": 5.0, "files_changed": 0,
                "lines_changed": 0, "cross_module": False}

    # Parse diff stats
    files_changed = set()
    lines_added = 0
    lines_deleted = 0
    packages = set()

    for line in patch.splitlines():
        if line.startswith("diff --git"):
            # Extract file path
            parts = line.split()
            if len(parts) >= 3:
                file_path = parts[2].removeprefix("a/")
                files_changed.add(file_path)
                # Extract top-level package
                pkg = file_path.split("/")[0] if "/" in file_path else ""
                if pkg:
                    packages.add(pkg)
        elif line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
        elif line.startswith("-") and not line.startswith("---"):
            lines_deleted += 1

    total_lines = lines_added + lines_deleted
    n_files = len(files_changed)
    cross_module = len(packages) > 1

    # Complexity score
    score = n_files * 2 + total_lines / 100 + (3 if cross_module else 0)

    if n_files <= 1 and total_lines < 100 and not cross_module:
        level = "simple"
    elif n_files >= 4 or total_lines > 300 or cross_module:
        level = "complex"
    else:
        level = "medium"

    return {
        "level": level,
        "score": round(score, 2),
        "files_changed": n_files,
        "lines_changed": total_lines,
        "cross_module": cross_module,
    }


def _stratified_sample(
    tasks: list[dict], n: int, seed: int = 42,
) -> list[dict]:
    """Stratified random sample: equal representation from each complexity level."""
    rng = random.Random(seed)

    by_level: dict[str, list[dict]] = {"simple": [], "medium": [], "complex": []}
    for t in tasks:
        level = t.get("complexity", "medium")
        by_level.setdefault(level, []).append(t)

    # Target: n/3 per level (round up for remainder)
    per_level = n // 3
    remainder = n - per_level * 3

    result = []
    for i, level in enumerate(["simple", "medium", "complex"]):
        available = by_level.get(level, [])
        target = per_level + (1 if i < remainder else 0)
        target = min(target, len(available))
        result.extend(rng.sample(available, target))

    rng.shuffle(result)
    return result


# ── Instance Setup ───────────────────────────────────────


def setup_swebench_instance(task: dict[str, Any]) -> Path:
    """Clone repo at the correct commit for a SWE-bench instance.

    Returns the workspace path.
    """
    instance_id = task["instance_id"]
    repo = task["repo"]
    base_commit = task["base_commit"]
    task_id = task["task_id"]

    workspace = INSTANCES_DIR / task_id
    if workspace.exists() and (workspace / ".git").exists():
        _log.info("Instance already set up: %s", workspace)
        return workspace

    workspace.mkdir(parents=True, exist_ok=True)

    # Clone (shallow)
    repo_url = f"https://github.com/{repo}.git"
    _log.info("Cloning %s at %s...", repo, base_commit[:8])

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(workspace)],
            capture_output=True, text=True, timeout=300,
            check=True,
        )
        # Fetch the specific commit
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", base_commit],
            capture_output=True, text=True, timeout=300,
            cwd=str(workspace), check=True,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace), check=True,
        )
    except subprocess.CalledProcessError as e:
        _log.error("Git operation failed: %s\n%s", e, e.stderr)
        raise RuntimeError(f"Failed to set up instance {instance_id}: {e}") from e

    # Try to install the repo as editable package
    try:
        setup_files = list(workspace.glob("setup.py")) + list(workspace.glob("setup.cfg")) + list(workspace.glob("pyproject.toml"))
        if setup_files:
            _log.info("Installing repo dependencies (pip install -e .) ...")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", "."],
                capture_output=True, text=True, timeout=300,
                cwd=str(workspace),
            )
            if r.returncode != 0:
                _log.warning("pip install -e . failed (non-blocking): %s", r.stderr[:200])
    except Exception as e:
        _log.warning("pip install -e . skipped: %s", e)

    # Write requirement.txt for experiment runner compatibility
    req_file = workspace / "requirement.txt"
    req_file.write_text(task["requirement"], encoding="utf-8")

    # Write test patch for evaluation
    if task.get("test_patch"):
        test_patch_file = workspace / "test_patch.diff"
        test_patch_file.write_text(task["test_patch"], encoding="utf-8")

    _log.info("Instance ready: %s", workspace)
    return workspace


# ── Evaluation ───────────────────────────────────────────


def evaluate_swebench_instance(
    task: dict[str, Any],
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Evaluate a SWE-bench instance by applying the test patch and running tests.

    Returns metrics dict compatible with experiment_runner_v2.
    """
    task_id = task["task_id"]
    workspace = workspace or INSTANCES_DIR / task_id

    if not workspace.exists():
        return {"total": 0, "passed": 0, "failed": 0, "error": "workspace not found"}

    test_patch = task.get("test_patch", "")
    if not test_patch:
        return {"total": 0, "passed": 0, "failed": 0, "error": "no test patch"}

    # Apply test patch
    test_patch_file = workspace / "test_patch.diff"
    if not test_patch_file.exists():
        test_patch_file.write_text(test_patch, encoding="utf-8")

    try:
        # Check if patch is already applied
        check = subprocess.run(
            ["git", "apply", "--check", "--reverse", str(test_patch_file)],
            capture_output=True, text=True, timeout=30,
            cwd=str(workspace),
        )
        if check.returncode != 0:
            # Patch not applied yet, apply it
            subprocess.run(
                ["git", "apply", str(test_patch_file)],
                capture_output=True, text=True, timeout=30,
                cwd=str(workspace), check=True,
            )
    except subprocess.CalledProcessError as e:
        _log.warning("Failed to apply test patch: %s", e.stderr)
        return {"total": 0, "passed": 0, "failed": 0, "error": f"patch failed: {e.stderr}"}

    # Run pytest on the test files mentioned in the patch
    test_files = _extract_test_files_from_patch(test_patch)
    if not test_files:
        # Fallback: run all tests
        test_files = ["tests/"]

    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", *test_files, "-v", "--tb=short", "-x"],
            capture_output=True, text=True, timeout=300,
            cwd=str(workspace),
            env={**os.environ, "PYTHONPATH": str(workspace)},
        )
        passed = r.stdout.count(" PASSED")
        failed = r.stdout.count(" FAILED") + r.stdout.count(" ERROR")
        return {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "resolve_rate": failed == 0 and passed > 0,
        }
    except subprocess.TimeoutExpired:
        return {"total": 0, "passed": 0, "failed": 0, "error": "test timeout"}
    except Exception as e:
        return {"total": 0, "passed": 0, "failed": 0, "error": str(e)}


def _extract_test_files_from_patch(patch: str) -> list[str]:
    """Extract test file paths from a diff patch."""
    files = []
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 3:
                file_path = parts[2].removeprefix("a/")
                if "test" in file_path.lower():
                    files.append(file_path)
    return files


# ── Requirement Conversion ───────────────────────────────


def swebench_to_experiment_tasks(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert SWE-bench tasks to experiment runner format.

    Creates requirement.txt and test_gt files in the expected structure.
    """
    experiment_tasks = []
    for task in tasks:
        task_dir = INSTANCES_DIR / task["task_id"]
        task_dir.mkdir(parents=True, exist_ok=True)

        # Write requirement
        req_file = task_dir / "requirement.txt"
        req_file.write_text(task["requirement"], encoding="utf-8")

        # Write metadata
        meta_file = task_dir / "metadata.json"
        meta = {
            "instance_id": task["instance_id"],
            "repo": task["repo"],
            "base_commit": task["base_commit"],
            "complexity": task["complexity"],
            "complexity_score": task["complexity_score"],
        }
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        experiment_tasks.append({
            "task_id": task["task_id"],
            "requirement": task["requirement"],
            "has_ground_truth": True,
            "task_dir": str(task_dir),
            "complexity": task["complexity"],
            "complexity_score": task["complexity_score"],
        })

    return experiment_tasks


# ── CLI ──────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SWE-bench Adapter for MyGO experiments")
    parser.add_argument("--split", default="verified", choices=["verified", "lite"])
    parser.add_argument("--list", action="store_true", help="List available tasks")
    parser.add_argument("--sample", type=int, help="Stratified sample of N tasks")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--complexity", choices=["simple", "medium", "complex"])
    parser.add_argument("--setup", type=str, help="Setup a specific instance by ID")
    parser.add_argument("--setup-all", action="store_true", help="Clone all sampled instances")
    parser.add_argument("--evaluate", type=str, help="Evaluate a specific instance")
    parser.add_argument("--export", type=Path, help="Export tasks to experiment directory")
    parser.add_argument("--stats", action="store_true", help="Show dataset statistics")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    tasks = load_swebench_tasks(
        split=args.split,
        sample_size=args.sample,
        seed=args.seed,
        complexity_filter=args.complexity,
    )

    if args.stats:
        by_level = {"simple": 0, "medium": 0, "complex": 0}
        for t in tasks:
            by_level[t["complexity"]] = by_level.get(t["complexity"], 0) + 1
        print(f"\nSWE-bench {args.split} statistics:")
        print(f"  Total tasks: {len(tasks)}")
        for level, count in sorted(by_level.items()):
            print(f"  {level}: {count}")
        repos = set(t["repo"] for t in tasks)
        print(f"  Unique repos: {len(repos)}")
        return

    if args.list:
        print(f"\nSWE-bench {args.split} tasks ({len(tasks)} total):")
        for t in tasks[:50]:  # show first 50
            print(
                f"  {t['task_id']:<50} {t['complexity']:<8} "
                f"score={t['complexity_score']:<6} {t['repo']}"
            )
        if len(tasks) > 50:
            print(f"  ... and {len(tasks) - 50} more")
        return

    if args.setup_all:
        print(f"Setting up {len(tasks)} instances...")
        ok, fail = 0, 0
        for t in tasks:
            try:
                setup_swebench_instance(t)
                ok += 1
            except Exception as e:
                print(f"  FAILED {t['instance_id']}: {e}")
                fail += 1
        print(f"Setup complete: {ok} ok, {fail} failed")
        return

    if args.setup:
        task = next((t for t in tasks if args.setup in t["instance_id"]), None)
        if not task:
            # Try to reload without sample
            all_tasks = load_swebench_tasks(split=args.split)
            task = next((t for t in all_tasks if args.setup in t["instance_id"]), None)
        if not task:
            print(f"Instance not found: {args.setup}")
            sys.exit(1)
        workspace = setup_swebench_instance(task)
        print(f"Instance ready at: {workspace}")
        return

    if args.evaluate:
        task = next((t for t in tasks if args.evaluate in t["instance_id"]), None)
        if not task:
            all_tasks = load_swebench_tasks(split=args.split)
            task = next((t for t in all_tasks if args.evaluate in t["instance_id"]), None)
        if not task:
            print(f"Instance not found: {args.evaluate}")
            sys.exit(1)
        result = evaluate_swebench_instance(task)
        print(f"Evaluation result: {json.dumps(result, indent=2)}")
        return

    if args.export:
        exp_tasks = swebench_to_experiment_tasks(tasks)
        print(f"Exported {len(exp_tasks)} tasks to {INSTANCES_DIR}")
        # Print distribution
        by_level = {"simple": 0, "medium": 0, "complex": 0}
        for t in exp_tasks:
            by_level[t["complexity"]] = by_level.get(t["complexity"], 0) + 1
        print(f"Distribution: {by_level}")
        return

    # Default: show stats
    print(f"Loaded {len(tasks)} tasks. Use --list, --stats, --sample, etc.")


if __name__ == "__main__":
    main()
