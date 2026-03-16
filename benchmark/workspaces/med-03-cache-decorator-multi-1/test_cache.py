"""Tests for cache decorator module."""

import time
import threading
import pytest
from cache import ttl_cache, memoize


# ---------------------------------------------------------------------------
# ttl_cache basic tests
# ---------------------------------------------------------------------------

class TestTTLCache:

    def test_basic_caching(self):
        call_count = 0

        @ttl_cache(max_size=128, ttl_seconds=60)
        def add(a, b):
            nonlocal call_count
            call_count += 1
            return a + b

        assert add(1, 2) == 3
        assert add(1, 2) == 3
        assert call_count == 1
        info = add.cache_info()
        assert info["hits"] == 1
        assert info["misses"] == 1
        assert info["size"] == 1

    def test_different_args(self):
        @ttl_cache()
        def square(n):
            return n * n

        assert square(3) == 9
        assert square(4) == 16
        info = square.cache_info()
        assert info["misses"] == 2
        assert info["size"] == 2

    def test_kwargs(self):
        call_count = 0

        @ttl_cache()
        def greet(name, greeting="hello"):
            nonlocal call_count
            call_count += 1
            return f"{greeting} {name}"

        assert greet("alice", greeting="hi") == "hi alice"
        assert greet("alice", greeting="hi") == "hi alice"
        assert call_count == 1

        # Different kwargs produce different cache entries
        assert greet("alice", greeting="hey") == "hey alice"
        assert call_count == 2

    def test_ttl_expiration(self):
        call_count = 0

        @ttl_cache(max_size=128, ttl_seconds=0.1)
        def value():
            nonlocal call_count
            call_count += 1
            return call_count

        assert value() == 1
        assert value() == 1  # cached
        time.sleep(0.15)
        assert value() == 2  # expired, recomputed

    def test_lru_eviction(self):
        @ttl_cache(max_size=2, ttl_seconds=60)
        def identity(x):
            return x

        identity(1)
        identity(2)
        assert identity.cache_info()["size"] == 2

        # Adding a third should evict the LRU (key=1)
        identity(3)
        assert identity.cache_info()["size"] == 2

        # key=1 was evicted, calling it again is a miss
        identity(1)
        info = identity.cache_info()
        assert info["misses"] == 4  # 1,2,3,1 are all misses

    def test_lru_ordering(self):
        """Access refreshes LRU order."""
        @ttl_cache(max_size=2, ttl_seconds=60)
        def identity(x):
            return x

        identity(1)  # miss
        identity(2)  # miss, cache=[1,2]
        identity(1)  # hit, cache=[2,1] (1 is now most recent)
        identity(3)  # miss, evicts 2 (LRU), cache=[1,3]

        # 1 should still be cached
        identity(1)  # hit
        info = identity.cache_info()
        assert info["hits"] == 2
        assert info["misses"] == 3

    def test_cache_clear(self):
        @ttl_cache()
        def identity(x):
            return x

        identity(1)
        identity(2)
        assert identity.cache_info()["size"] == 2

        identity.cache_clear()
        info = identity.cache_info()
        assert info["size"] == 0
        assert info["hits"] == 0
        assert info["misses"] == 0

    def test_cache_info_max_size(self):
        @ttl_cache(max_size=50)
        def noop():
            pass

        noop()
        assert noop.cache_info()["max_size"] == 50

    def test_unhashable_args_raise_type_error(self):
        @ttl_cache()
        def identity(x):
            return x

        with pytest.raises(TypeError):
            identity([1, 2, 3])

    def test_unhashable_kwargs_raise_type_error(self):
        @ttl_cache()
        def identity(x):
            return x

        with pytest.raises(TypeError):
            identity(x={"a": 1})

    def test_preserves_function_metadata(self):
        @ttl_cache()
        def my_func():
            """My docstring."""
            pass

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "My docstring."

    def test_invalid_max_size_raises(self):
        with pytest.raises(ValueError):
            @ttl_cache(max_size=0)
            def noop():
                pass

        with pytest.raises(ValueError):
            @ttl_cache(max_size=-1)
            def noop2():
                pass

    def test_invalid_ttl_raises(self):
        with pytest.raises(ValueError):
            @ttl_cache(ttl_seconds=0)
            def noop():
                pass

        with pytest.raises(ValueError):
            @ttl_cache(ttl_seconds=-5)
            def noop2():
                pass

    def test_expired_entries_evicted_on_access(self):
        """Expired entries should be cleaned up when new entries are inserted."""
        @ttl_cache(max_size=3, ttl_seconds=0.05)
        def identity(x):
            return x

        identity(1)
        identity(2)
        identity(3)
        assert identity.cache_info()["size"] == 3

        time.sleep(0.1)  # all entries expire

        # Next call should evict expired entries and insert new one
        identity(4)
        assert identity.cache_info()["size"] == 1

    def test_max_size_one(self):
        """Edge case: cache with max_size=1."""
        @ttl_cache(max_size=1, ttl_seconds=60)
        def identity(x):
            return x

        identity(1)
        assert identity.cache_info()["size"] == 1
        identity(2)
        assert identity.cache_info()["size"] == 1
        # 1 was evicted
        identity(1)
        assert identity.cache_info()["misses"] == 3


# ---------------------------------------------------------------------------
# memoize tests
# ---------------------------------------------------------------------------

class TestMemoize:

    def test_basic_memoize(self):
        call_count = 0

        @memoize
        def double(n):
            nonlocal call_count
            call_count += 1
            return n * 2

        assert double(5) == 10
        assert double(5) == 10
        assert call_count == 1
        info = double.cache_info()
        assert info["hits"] == 1
        assert info["misses"] == 1

    def test_no_size_limit(self):
        @memoize
        def identity(x):
            return x

        for i in range(500):
            identity(i)

        assert identity.cache_info()["size"] == 500
        assert identity.cache_info()["max_size"] is None

    def test_no_ttl_expiration(self):
        call_count = 0

        @memoize
        def value():
            nonlocal call_count
            call_count += 1
            return call_count

        assert value() == 1
        time.sleep(0.05)
        assert value() == 1  # should still be cached (no TTL)

    def test_memoize_cache_clear(self):
        @memoize
        def identity(x):
            return x

        identity(1)
        identity(2)
        assert identity.cache_info()["size"] == 2

        identity.cache_clear()
        assert identity.cache_info()["size"] == 0
        assert identity.cache_info()["hits"] == 0
        assert identity.cache_info()["misses"] == 0

    def test_memoize_preserves_metadata(self):
        @memoize
        def my_func():
            """My docstring."""
            pass

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "My docstring."

    def test_memoize_unhashable_args_raise_type_error(self):
        @memoize
        def identity(x):
            return x

        with pytest.raises(TypeError):
            identity([1, 2, 3])


# ---------------------------------------------------------------------------
# Thread safety tests
# ---------------------------------------------------------------------------

class TestThreadSafety:

    def test_concurrent_access(self):
        call_count = 0

        @ttl_cache(max_size=128, ttl_seconds=60)
        def slow_square(n):
            nonlocal call_count
            call_count += 1
            time.sleep(0.01)
            return n * n

        results = []
        results_lock = threading.Lock()
        errors = []

        def worker(val):
            try:
                r = slow_square(val)
                with results_lock:
                    results.append((val, r))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i % 5,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 20
        # Every result should be the correct square of its input
        for val, r in results:
            assert r == val * val

    def test_concurrent_cache_clear(self):
        """Clearing cache while other threads are accessing should not crash."""
        @ttl_cache(max_size=128, ttl_seconds=60)
        def identity(x):
            return x

        errors = []

        def reader():
            try:
                for i in range(50):
                    identity(i % 10)
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                for _ in range(10):
                    identity.cache_clear()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads.append(threading.Thread(target=clearer))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
