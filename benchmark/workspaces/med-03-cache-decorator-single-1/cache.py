"""Cache decorator with TTL-based expiration and LRU eviction."""

import time
import functools
from collections import OrderedDict


def _make_key(args, kwargs):
    """Create a hashable key from function arguments."""
    key = args
    if kwargs:
        key += (object,)  # sentinel separating args from kwargs
        for item in sorted(kwargs.items()):
            key += item
    # Force a hash check to raise TypeError for unhashable args
    hash(key)
    return key


def ttl_cache(max_size: int = 128, ttl_seconds: float = 60):
    """Decorator factory that caches function results with TTL and LRU eviction."""

    def decorator(func):
        cache = OrderedDict()  # key -> (result, timestamp)
        hits = 0
        misses = 0

        def _evict_expired():
            """Remove all expired entries."""
            now = time.monotonic()
            expired_keys = [
                k for k, (_, ts) in cache.items()
                if ttl_seconds is not None and (now - ts) >= ttl_seconds
            ]
            for k in expired_keys:
                del cache[k]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal hits, misses
            key = _make_key(args, kwargs)

            # Evict expired entries on access
            _evict_expired()

            if key in cache:
                # Move to end (most recently used)
                cache.move_to_end(key)
                hits += 1
                return cache[key][0]

            # Cache miss
            misses += 1
            result = func(*args, **kwargs)

            # If at max capacity, evict LRU (first item)
            if max_size is not None and len(cache) >= max_size:
                cache.popitem(last=False)

            cache[key] = (result, time.monotonic())
            return result

        def cache_info():
            return {
                "hits": hits,
                "misses": misses,
                "size": len(cache),
                "max_size": max_size,
            }

        def cache_clear():
            nonlocal hits, misses
            cache.clear()
            hits = 0
            misses = 0

        wrapper.cache_info = cache_info
        wrapper.cache_clear = cache_clear
        return wrapper

    return decorator


def memoize(func):
    """Simple memoization decorator with no TTL and no size limit."""
    cache = {}
    hits = 0
    misses = 0

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        nonlocal hits, misses
        key = _make_key(args, kwargs)

        if key in cache:
            hits += 1
            return cache[key]

        misses += 1
        result = func(*args, **kwargs)
        cache[key] = result
        return result

    def cache_info():
        return {
            "hits": hits,
            "misses": misses,
            "size": len(cache),
            "max_size": None,
        }

    def cache_clear():
        nonlocal hits, misses
        cache.clear()
        hits = 0
        misses = 0

    wrapper.cache_info = cache_info
    wrapper.cache_clear = cache_clear
    return wrapper
