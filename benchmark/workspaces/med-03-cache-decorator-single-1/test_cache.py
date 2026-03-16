"""Tests for cache decorators."""

import time
import pytest
from cache import ttl_cache, memoize


# ---- ttl_cache tests ----

class TestTtlCache:
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

    def test_cache_info(self):
        @ttl_cache()
        def square(x):
            return x * x

        square(3)
        square(3)
        square(4)
        info = square.cache_info()
        assert info["hits"] == 1
        assert info["misses"] == 2
        assert info["size"] == 2
        assert info["max_size"] == 128

    def test_cache_clear(self):
        @ttl_cache()
        def inc(x):
            return x + 1

        inc(1)
        inc(2)
        inc.cache_clear()
        info = inc.cache_info()
        assert info["hits"] == 0
        assert info["misses"] == 0
        assert info["size"] == 0

    def test_ttl_expiration(self):
        call_count = 0

        @ttl_cache(max_size=128, ttl_seconds=0.1)
        def greet(name):
            nonlocal call_count
            call_count += 1
            return f"hello {name}"

        assert greet("alice") == "hello alice"
        assert call_count == 1
        assert greet("alice") == "hello alice"
        assert call_count == 1  # cached

        time.sleep(0.15)

        assert greet("alice") == "hello alice"
        assert call_count == 2  # expired, recomputed

    def test_lru_eviction(self):
        @ttl_cache(max_size=2, ttl_seconds=60)
        def identity(x):
            return x

        identity(1)
        identity(2)
        identity(3)  # evicts key 1
        info = identity.cache_info()
        assert info["size"] == 2

    def test_kwargs(self):
        call_count = 0

        @ttl_cache()
        def f(a, b=10):
            nonlocal call_count
            call_count += 1
            return a + b

        assert f(1, b=2) == 3
        assert f(1, b=2) == 3
        assert call_count == 1
        assert f(1, b=3) == 4
        assert call_count == 2

    def test_unhashable_args_raises(self):
        @ttl_cache()
        def f(x):
            return x

        with pytest.raises(TypeError):
            f([1, 2, 3])


# ---- memoize tests ----

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
        # Without memoize this would be exponential calls
        assert call_count == 11  # each n from 0..10 computed once

    def test_cache_info(self):
        @memoize
        def double(x):
            return x * 2

        double(1)
        double(2)
        double(1)
        info = double.cache_info()
        assert info["hits"] == 1
        assert info["misses"] == 2
        assert info["size"] == 2
        assert info["max_size"] is None

    def test_cache_clear(self):
        @memoize
        def noop(x):
            return x

        noop(1)
        noop.cache_clear()
        info = noop.cache_info()
        assert info["hits"] == 0
        assert info["misses"] == 0
        assert info["size"] == 0

    def test_no_size_limit(self):
        @memoize
        def identity(x):
            return x

        for i in range(500):
            identity(i)
        assert identity.cache_info()["size"] == 500

    def test_unhashable_args_raises(self):
        @memoize
        def f(x):
            return x

        with pytest.raises(TypeError):
            f({"a": 1})
