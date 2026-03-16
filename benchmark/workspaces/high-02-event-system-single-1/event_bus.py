"""Publish-subscribe event bus system."""

import threading
import time
import uuid
from typing import Callable


class Event:
    """Represents an event fired through the event bus."""

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
    """A publish-subscribe event bus supporting prioritized, thread-safe handler dispatch."""

    def __init__(self):
        self._subscribers: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self._insertion_order = 0

    def subscribe(self, event_type: str, handler: Callable, priority: int = 0) -> str:
        """Register a handler for an event type. Returns a subscription ID."""
        subscription_id = str(uuid.uuid4())
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append({
                "id": subscription_id,
                "handler": handler,
                "priority": priority,
                "event_type": event_type,
                "order": self._insertion_order,
            })
            self._insertion_order += 1
            # Keep sorted by priority descending, stable on insertion order
            self._subscribers[event_type].sort(
                key=lambda s: (-s["priority"], s["order"])
            )
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription by ID. Returns False if not found."""
        with self._lock:
            for event_type, subs in self._subscribers.items():
                for i, sub in enumerate(subs):
                    if sub["id"] == subscription_id:
                        subs.pop(i)
                        return True
        return False

    def publish(self, event_type: str, data: dict | None = None) -> int:
        """Fire event to all subscribers. Returns number of handlers called."""
        event = Event(event_type, data)
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))
        count = 0
        for sub in handlers:
            if event.is_propagation_stopped:
                break
            try:
                sub["handler"](event)
            except Exception:
                pass
            count += 1
        return count

    def publish_async(self, event_type: str, data: dict | None = None) -> int:
        """Fire event using threads for each handler. Returns count immediately."""
        event = Event(event_type, data)
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))
        threads = []
        for sub in handlers:
            if event.is_propagation_stopped:
                break
            t = threading.Thread(target=self._run_handler, args=(sub["handler"], event))
            threads.append(t)
            t.start()
        return len(threads)

    @staticmethod
    def _run_handler(handler: Callable, event: Event) -> None:
        try:
            handler(event)
        except Exception:
            pass

    def clear(self, event_type: str | None = None) -> None:
        """Clear subscriptions for a type, or all if None."""
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
                    "id": s["id"],
                    "event_type": s["event_type"],
                    "priority": s["priority"],
                }
                for s in subs
            ]


def event_handler(event_type: str, priority: int = 0):
    """Decorator to mark a function as an event handler for auto-registration."""
    def decorator(func: Callable) -> Callable:
        func._event_handler_event_type = event_type
        func._event_handler_priority = priority
        return func
    return decorator
