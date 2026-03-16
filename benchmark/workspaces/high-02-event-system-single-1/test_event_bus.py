"""Tests for the event bus system."""

import time
import threading
import pytest
from event_bus import EventBus, Event, event_handler


class TestEvent:
    def test_event_properties(self):
        e = Event("click", {"x": 10})
        assert e.event_type == "click"
        assert e.data == {"x": 10}
        assert isinstance(e.timestamp, float)
        assert isinstance(e.id, str) and len(e.id) > 0

    def test_event_default_data(self):
        e = Event("ping")
        assert e.data == {}

    def test_stop_propagation(self):
        e = Event("test")
        assert e.is_propagation_stopped is False
        e.stop_propagation()
        assert e.is_propagation_stopped is True


class TestEventBus:
    def setup_method(self):
        self.bus = EventBus()

    def test_subscribe_returns_id(self):
        sid = self.bus.subscribe("click", lambda e: None)
        assert isinstance(sid, str) and len(sid) > 0

    def test_publish_calls_handler(self):
        results = []
        self.bus.subscribe("click", lambda e: results.append(e.event_type))
        count = self.bus.publish("click", {"x": 1})
        assert count == 1
        assert results == ["click"]

    def test_publish_returns_zero_for_no_subscribers(self):
        assert self.bus.publish("nope") == 0

    def test_multiple_handlers(self):
        results = []
        self.bus.subscribe("e", lambda e: results.append(1))
        self.bus.subscribe("e", lambda e: results.append(2))
        count = self.bus.publish("e")
        assert count == 2
        assert len(results) == 2

    def test_priority_order(self):
        results = []
        self.bus.subscribe("e", lambda e: results.append("low"), priority=1)
        self.bus.subscribe("e", lambda e: results.append("high"), priority=10)
        self.bus.subscribe("e", lambda e: results.append("mid"), priority=5)
        self.bus.publish("e")
        assert results == ["high", "mid", "low"]

    def test_stable_order_same_priority(self):
        results = []
        self.bus.subscribe("e", lambda e: results.append("first"), priority=0)
        self.bus.subscribe("e", lambda e: results.append("second"), priority=0)
        self.bus.subscribe("e", lambda e: results.append("third"), priority=0)
        self.bus.publish("e")
        assert results == ["first", "second", "third"]

    def test_unsubscribe(self):
        results = []
        sid = self.bus.subscribe("e", lambda e: results.append(1))
        assert self.bus.unsubscribe(sid) is True
        self.bus.publish("e")
        assert results == []

    def test_unsubscribe_not_found(self):
        assert self.bus.unsubscribe("nonexistent-id") is False

    def test_stop_propagation_prevents_later_handlers(self):
        results = []

        def stopper(e):
            results.append("stopper")
            e.stop_propagation()

        self.bus.subscribe("e", stopper, priority=10)
        self.bus.subscribe("e", lambda e: results.append("after"), priority=1)
        count = self.bus.publish("e")
        assert count == 1
        assert results == ["stopper"]

    def test_handler_exception_doesnt_stop_others(self):
        results = []

        def bad_handler(e):
            raise ValueError("boom")

        self.bus.subscribe("e", bad_handler, priority=10)
        self.bus.subscribe("e", lambda e: results.append("ok"), priority=1)
        count = self.bus.publish("e")
        assert count == 2
        assert results == ["ok"]

    def test_clear_specific_event(self):
        self.bus.subscribe("a", lambda e: None)
        self.bus.subscribe("b", lambda e: None)
        self.bus.clear("a")
        assert self.bus.get_subscribers("a") == []
        assert len(self.bus.get_subscribers("b")) == 1

    def test_clear_all(self):
        self.bus.subscribe("a", lambda e: None)
        self.bus.subscribe("b", lambda e: None)
        self.bus.clear()
        assert self.bus.get_subscribers("a") == []
        assert self.bus.get_subscribers("b") == []

    def test_get_subscribers(self):
        sid = self.bus.subscribe("e", lambda e: None, priority=5)
        subs = self.bus.get_subscribers("e")
        assert len(subs) == 1
        assert subs[0]["id"] == sid
        assert subs[0]["priority"] == 5
        assert subs[0]["event_type"] == "e"

    def test_get_subscribers_empty(self):
        assert self.bus.get_subscribers("nope") == []

    def test_publish_async(self):
        results = []
        lock = threading.Lock()

        def handler(e):
            with lock:
                results.append(e.event_type)

        self.bus.subscribe("e", handler)
        self.bus.subscribe("e", handler)
        count = self.bus.publish_async("e")
        assert count == 2
        # Wait briefly for threads to finish
        time.sleep(0.1)
        assert len(results) == 2

    def test_publish_async_exception_handled(self):
        def bad(e):
            raise RuntimeError("fail")

        self.bus.subscribe("e", bad)
        count = self.bus.publish_async("e")
        assert count == 1
        time.sleep(0.05)

    def test_publish_passes_data(self):
        received = []
        self.bus.subscribe("e", lambda e: received.append(e.data))
        self.bus.publish("e", {"key": "value"})
        assert received == [{"key": "value"}]

    def test_thread_safe_subscribe(self):
        """Concurrent subscribes should not lose entries."""
        ids = []
        lock = threading.Lock()

        def do_subscribe():
            sid = self.bus.subscribe("e", lambda e: None)
            with lock:
                ids.append(sid)

        threads = [threading.Thread(target=do_subscribe) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == 50
        assert len(self.bus.get_subscribers("e")) == 50


class TestEventHandlerDecorator:
    def test_decorator_marks_function(self):
        @event_handler("click", priority=5)
        def on_click(event):
            pass

        assert on_click._event_handler_event_type == "click"
        assert on_click._event_handler_priority == 5

    def test_decorator_default_priority(self):
        @event_handler("hover")
        def on_hover(event):
            pass

        assert on_hover._event_handler_event_type == "hover"
        assert on_hover._event_handler_priority == 0

    def test_decorator_auto_register(self):
        bus = EventBus()

        @event_handler("test_event", priority=3)
        def my_handler(event):
            pass

        # Auto-register using the metadata
        bus.subscribe(
            my_handler._event_handler_event_type,
            my_handler,
            my_handler._event_handler_priority,
        )
        subs = bus.get_subscribers("test_event")
        assert len(subs) == 1
        assert subs[0]["priority"] == 3
