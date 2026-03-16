"""Publish-subscribe event bus system with priority-based handler execution."""

import functools
import threading
import uuid
import time
from typing import Callable


class Event:
    """Represents an event fired through the EventBus."""

    def __init__(self, event_type: str, data: dict | None = None):
        self._event_type = event_type
        self._data = data if data is not None else {}
        self._timestamp = time.time()
        self._id = str(uuid.uuid4())
        self._propagation_stopped = False

    @property
    def event_type(self) -> str:
        return self._event_type

    @property
    def data(self) -> dict:
        return self._data

    @property
    def timestamp(self) -> float:
        return self._timestamp

    @property
    def id(self) -> str:
        return self._id

    def stop_propagation(self) -> None:
        """Prevent remaining handlers from being called."""
        self._propagation_stopped = True

    @property
    def is_propagation_stopped(self) -> bool:
        return self._propagation_stopped


class EventBus:
    """A thread-safe publish-subscribe event bus with priority-based dispatch."""

    def __init__(self):
        self._subscribers: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Callable, priority: int = 0) -> str:
        """Register handler for event type. Higher priority runs first.
        Returns subscription_id (UUID)."""
        subscription_id = str(uuid.uuid4())
        entry = {
            "subscription_id": subscription_id,
            "handler": handler,
            "priority": priority,
            "event_type": event_type,
        }
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(entry)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove subscription. Returns False if not found."""
        with self._lock:
            for event_type, subs in self._subscribers.items():
                for i, entry in enumerate(subs):
                    if entry["subscription_id"] == subscription_id:
                        subs.pop(i)
                        return True
        return False

    def publish(self, event_type: str, data: dict | None = None) -> int:
        """Fire event to all subscribers. Returns number of handlers called.
        Handlers receive an Event object."""
        event = Event(event_type, data)
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))
        # Sort by priority descending; stable sort preserves insertion order for ties
        handlers.sort(key=lambda h: h["priority"], reverse=True)
        count = 0
        for entry in handlers:
            if event.is_propagation_stopped:
                break
            try:
                entry["handler"](event)
                count += 1
            except Exception:
                # Handler exceptions don't stop other handlers
                count += 1
        return count

    def publish_async(self, event_type: str, data: dict | None = None) -> int:
        """Same as publish but uses threading for each handler.
        Returns count immediately."""
        event = Event(event_type, data)
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))
        handlers.sort(key=lambda h: h["priority"], reverse=True)
        count = 0
        for entry in handlers:
            if event.is_propagation_stopped:
                break
            count += 1
            thread = threading.Thread(target=self._safe_call, args=(entry["handler"], event))
            thread.daemon = True
            thread.start()
        return count

    @staticmethod
    def _safe_call(handler: Callable, event: Event) -> None:
        """Call handler, swallowing exceptions."""
        try:
            handler(event)
        except Exception:
            pass

    def clear(self, event_type: str | None = None) -> None:
        """Clear subscriptions for type, or all if None."""
        with self._lock:
            if event_type is None:
                self._subscribers.clear()
            else:
                self._subscribers.pop(event_type, None)

    def get_subscribers(self, event_type: str) -> list[dict]:
        """Return list of subscriber info dicts."""
        with self._lock:
            subs = self._subscribers.get(event_type, [])
            return [
                {
                    "subscription_id": s["subscription_id"],
                    "handler": s["handler"],
                    "priority": s["priority"],
                    "event_type": s["event_type"],
                }
                for s in subs
            ]


def event_handler(event_type: str, priority: int = 0):
    """Decorator to mark a function as an event handler.
    The decorated function gains an `_event_handler_info` attribute
    that can be used to auto-register it with an EventBus."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        wrapper._event_handler_info = {  # type: ignore[attr-defined]
            "event_type": event_type,
            "priority": priority,
        }
        return wrapper
    return decorator
