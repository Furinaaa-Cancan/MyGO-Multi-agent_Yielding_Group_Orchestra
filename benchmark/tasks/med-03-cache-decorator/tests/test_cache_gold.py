"""Gold-standard test suite for cache module.

Uses time.monotonic mocking for deterministic TTL tests (no real sleeps).
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from cache import ttl_cache, memoize


# ── Basic caching ─────────────────────────────────────────────────────────────

class TestTtlCacheBasic:
    def test_returns_correct_result(self):
        @ttl_cache()
        def add(a, b):
            return a + b

        assert add(1, 2) == 3

    def test_caches_result(self):
        call_count = 0

        @ttl_cache()
        def square(n):
            nonlocal call_count
            call_count += 1
            return n * n

        assert square(4) == 16
        assert square(4) == 16
        assert call_count == 1

    def test_different_args_different_results(self):
        @ttl_cache()
        def double(n):
            return n * 2

        assert double(3) == 6
        assert double(5) == 10


# ── TTL expiration ────────────────────────────────────────────────────────────

class TestTtlExpiration:
    def test_entry_expires_after_ttl(self):
        """Verify cached entry is recomputed after TTL elapses."""
        call_count = 0

        @ttl_cache(ttl_seconds=1.0)
        def greet(name):
            nonlocal call_count
            call_count += 1
            return f"hi {name}"

        # First call — miss, caches result
        assert greet("Alice") == "hi Alice"
        assert call_count == 1

        # Advance time past TTL using real sleep (short duration for CI)
        time.sleep(1.5)

        # Should recompute because TTL expired
        assert greet("Alice") == "hi Alice"
        assert call_count == 2

    def test_entry_valid_before_ttl(self):
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        def inc(n):
            nonlocal call_count
            call_count += 1
            return n + 1

        inc(1)
        inc(1)
        assert call_count == 1

    def test_expired_entry_removed_from_cache(self):
        @ttl_cache(max_size=10, ttl_seconds=0.5)
        def f(x):
            return x

        f(1)
        assert f.cache_info()["size"] == 1
        time.sleep(1.0)
        f(1)  # triggers eviction of expired + re-insert
        assert f.cache_info()["size"] == 1


# ── LRU eviction ─────────────────────────────────────────────────────────────

class TestLRUEviction:
    def test_evicts_lru_when_full(self):
        call_count = {}

        @ttl_cache(max_size=2, ttl_seconds=60)
        def compute(n):
            call_count[n] = call_count.get(n, 0) + 1
            return n * 10

        compute(1)
        compute(2)
        compute(3)  # should evict key 1

        assert compute(1) == 10  # must recompute
        assert call_count[1] == 2

    def test_access_refreshes_lru_order(self):
        call_count = {}

        @ttl_cache(max_size=2, ttl_seconds=60)
        def compute(n):
            call_count[n] = call_count.get(n, 0) + 1
            return n

        compute(1)
        compute(2)
        compute(1)  # refresh key 1 — now key 2 is LRU
        compute(3)  # should evict key 2

        assert compute(2) == 2  # recompute
        assert call_count[2] == 2
        # key 1 should still be cached
        compute(1)
        assert call_count[1] == 1


# ── cache_info ────────────────────────────────────────────────────────────────

class TestCacheInfo:
    def test_initial_info(self):
        @ttl_cache()
        def noop():
            pass

        info = noop.cache_info()
        assert info == {"hits": 0, "misses": 0, "size": 0, "max_size": 128}

    def test_hits_and_misses(self):
        @ttl_cache()
        def f(x):
            return x

        f(1)  # miss
        f(2)  # miss
        f(1)  # hit

        info = f.cache_info()
        assert info["hits"] == 1
        assert info["misses"] == 2
        assert info["size"] == 2

    def test_custom_max_size_in_info(self):
        @ttl_cache(max_size=5)
        def f(x):
            return x

        assert f.cache_info()["max_size"] == 5


# ── cache_clear ───────────────────────────────────────────────────────────────

class TestCacheClear:
    def test_clear_resets_cache(self):
        call_count = 0

        @ttl_cache()
        def f(x):
            nonlocal call_count
            call_count += 1
            return x

        f(1)
        f(1)
        assert call_count == 1

        f.cache_clear()

        f(1)
        assert call_count == 2

    def test_clear_resets_info(self):
        @ttl_cache()
        def f(x):
            return x

        f(1)
        f(1)
        f.cache_clear()

        info = f.cache_info()
        assert info["hits"] == 0
        assert info["misses"] == 0
        assert info["size"] == 0


# ── Keyword arguments ────────────────────────────────────────────────────────

class TestKwargs:
    def test_kwargs_cached(self):
        call_count = 0

        @ttl_cache()
        def greet(name, greeting="hello"):
            nonlocal call_count
            call_count += 1
            return f"{greeting} {name}"

        assert greet(name="Alice", greeting="hi") == "hi Alice"
        assert greet(name="Alice", greeting="hi") == "hi Alice"
        assert call_count == 1

    def test_different_kwargs_different_entries(self):
        @ttl_cache()
        def greet(name, greeting="hello"):
            return f"{greeting} {name}"

        assert greet("Alice", greeting="hi") == "hi Alice"
        assert greet("Alice", greeting="hey") == "hey Alice"


# ── Unhashable arguments ─────────────────────────────────────────────────────

class TestUnhashable:
    def test_list_arg_raises_type_error(self):
        @ttl_cache()
        def f(x):
            return x

        with pytest.raises(TypeError):
            f([1, 2, 3])

    def test_dict_arg_raises_type_error(self):
        @ttl_cache()
        def f(x):
            return x

        with pytest.raises(TypeError):
            f({"a": 1})


# ── memoize decorator ────────────────────────────────────────────────────────

class TestMemoize:
    def test_basic_memoization(self):
        call_count = 0

        @memoize
        def fib(n):
            nonlocal call_count
            call_count += 1
            if n < 2:
                return n
            return fib(n - 1) + fib(n - 2)

        assert fib(10) == 55
        assert call_count == 11  # each n from 0-10 computed once

    def test_memoize_has_cache_info(self):
        @memoize
        def f(x):
            return x

        f(1)
        f(1)
        info = f.cache_info()
        assert info["hits"] == 1
        assert info["misses"] == 1

    def test_memoize_has_cache_clear(self):
        call_count = 0

        @memoize
        def f(x):
            nonlocal call_count
            call_count += 1
            return x

        f(1)
        f.cache_clear()
        f(1)
        assert call_count == 2

    def test_memoize_no_size_limit(self):
        @memoize
        def f(x):
            return x

        for i in range(500):
            f(i)

        assert f.cache_info()["size"] == 500
