# Task: Cache Decorator with TTL

Implement `cache.py` with the following:

1. `ttl_cache(max_size: int = 128, ttl_seconds: float = 60)` - A decorator factory that caches function results with TTL-based expiration and LRU eviction.
   - Expired entries are evicted on the next access.
   - When `max_size` is reached, evict the least recently used entry.
   - Works with both positional and keyword arguments (arguments must be hashable).
   - Raises `TypeError` for unhashable arguments.

2. Each cached function should expose the following attributes:
   - `cache_info() -> dict` - Returns `{"hits": int, "misses": int, "size": int, "max_size": int}`.
   - `cache_clear() -> None` - Clears all cached entries and resets hit/miss counters.

3. `memoize(func)` - A simple memoization decorator with no TTL and no size limit. Behaves like `@ttl_cache` but with infinite TTL and unlimited size. Should also expose `cache_info()` and `cache_clear()`.

Also create `test_cache.py` with basic tests.
