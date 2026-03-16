# Task: Event Bus System

Implement a publish-subscribe event bus system.

Files: `event_bus.py`, `test_event_bus.py`

## Classes

1. `EventBus`:
   - `subscribe(event_type: str, handler: Callable, priority: int = 0) -> str` - Register handler for event type. Higher priority runs first. Returns subscription_id (UUID).
   - `unsubscribe(subscription_id: str) -> bool` - Remove subscription. Returns False if not found.
   - `publish(event_type: str, data: dict | None = None) -> int` - Fire event to all subscribers. Returns number of handlers called. Handlers receive Event object.
   - `publish_async(event_type: str, data: dict | None = None) -> int` - Same but uses threading for each handler (still returns count immediately).
   - `clear(event_type: str | None = None)` - Clear subscriptions for type, or all if None.
   - `get_subscribers(event_type: str) -> list[dict]` - Return list of subscriber info dicts.

2. `Event`:
   - Properties: `event_type: str`, `data: dict`, `timestamp: float`, `id: str`
   - `stop_propagation()` - Prevent remaining handlers from being called
   - `is_propagation_stopped -> bool`

3. `event_handler(event_type: str, priority: int = 0)` - Decorator to auto-register handlers.

## Requirements

- Handlers execute in priority order (high to low, stable order for same priority)
- stop_propagation prevents subsequent handlers from running
- Thread-safe subscribe/unsubscribe
- Handler exceptions don't stop other handlers from executing
