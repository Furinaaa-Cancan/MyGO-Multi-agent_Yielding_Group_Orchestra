"""Cache decorator with TTL-based expiration and LRU eviction."""

import time
import threading
import functools
from collections import OrderedDict


_KWARGS_SENTINEL = object()  # unique sentinel to separate args from kwargs in key


def _make_key(args, kwargs):
    """Create a hashable cache key from function arguments.

    Raises TypeError if any argument is unhashable.
    """
    key = args
    if kwargs:
        key += (_KWARGS_SENTINEL,)
        for item in sorted(kwargs.items()):
            key += item
    # Validate hashability by actually hashing
    hash(key)
    return key


def ttl_cache(max_size: int = 128, ttl_seconds: float = 60):
    """Decorator factory that caches function results with TTL expiration and LRU eviction.

    Args:
        max_size: Maximum number of entries in the cache. None for unlimited.
        ttl_seconds: Time-to-live in seconds for each cache entry. None for no expiration.

    Returns:
        A decorator that wraps the target function with caching.

    Raises:
        ValueError: If max_size is not None and <= 0, or ttl_seconds is not None and <= 0.
    """
    if max_size is not None and max_size <= 0:
        raise ValueError("max_size must be a positive integer or None")
    if ttl_seconds is not None and ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be a positive number or None")

    def decorator(func):
        cache = OrderedDict()  # key -> (value, timestamp)
        lock = threading.Lock()
        hits = 0
        misses = 0

        def _is_expired(timestamp):
            if ttl_seconds is None:
                return False
            return (time.monotonic() - timestamp) >= ttl_seconds

        def _evict_expired():
            """Remove all expired entries from the cache."""
            expired_keys = [k for k, (v, ts) in cache.items() if _is_expired(ts)]
            for k in expired_keys:
                del cache[k]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal hits, misses

            key = _make_key(args, kwargs)

            with lock:
                if key in cache:
                    value, timestamp = cache[key]
                    if not _is_expired(timestamp):
                        # Move to end (most recently used)
                        cache.move_to_end(key)
                        hits += 1
                        return value
                    else:
                        # Entry is expired, remove it
                        del cache[key]

                misses += 1

            # Call function outside the lock to avoid holding the lock
            # during potentially long function calls. This means concurrent
            # calls with the same key may compute duplicates, which is an
            # acceptable trade-off for not blocking other callers.
            result = func(*args, **kwargs)

            with lock:
                # If another thread already inserted this key while we were
                # computing, just update it with our (equally valid) result.
                _evict_expired()

                if max_size is not None:
                    # If the key is already present (concurrent duplicate),
                    # remove it first so it doesn't count toward capacity.
                    if key in cache:
                        del cache[key]
                    while len(cache) >= max_size:
                        cache.popitem(last=False)

                cache[key] = (result, time.monotonic())
                cache.move_to_end(key)

            return result

        def cache_info():
            """Return cache statistics."""
            with lock:
                return {
                    "hits": hits,
                    "misses": misses,
                    "size": len(cache),
                    "max_size": max_size,
                }

        def cache_clear():
            """Clear the cache and reset statistics."""
            nonlocal hits, misses
            with lock:
                cache.clear()
                hits = 0
                misses = 0

        wrapper.cache_info = cache_info
        wrapper.cache_clear = cache_clear

        return wrapper

    return decorator


def memoize(func):
    """Simple memoization decorator with no TTL and no size limit.

    Behaves like @ttl_cache but with infinite TTL and unlimited size.
    """
    wrapped = ttl_cache(max_size=None, ttl_seconds=None)(func)
    # Preserve original function metadata
    functools.update_wrapper(wrapped, func)
    # Re-attach cache_info and cache_clear since update_wrapper may overwrite __dict__
    # (it doesn't by default, but be explicit)
    return wrapped
