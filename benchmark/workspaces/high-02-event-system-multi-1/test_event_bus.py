"""Tests for the event bus system."""

import time
import threading
import pytest
from event_bus import EventBus, Event, event_handler


class TestEvent:
    """Tests for the Event class."""

    def test_event_properties(self):
        event = Event("test_event", {"key": "value"})
        assert event.event_type == "test_event"
        assert event.data == {"key": "value"}
        assert isinstance(event.timestamp, float)
        assert isinstance(event.id, str)
        assert len(event.id) > 0

    def test_event_default_data(self):
        event = Event("test_event")
        assert event.data == {}

    def test_event_stop_propagation(self):
        event = Event("test_event")
        assert event.is_propagation_stopped is False
        event.stop_propagation()
        assert event.is_propagation_stopped is True

    def test_event_unique_ids(self):
        e1 = Event("test")
        e2 = Event("test")
        assert e1.id != e2.id


class TestEventBusSubscribe:
    """Tests for subscribe/unsubscribe."""

    def test_subscribe_returns_uuid(self):
        bus = EventBus()
        sid = bus.subscribe("click", lambda e: None)
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_unsubscribe_existing(self):
        bus = EventBus()
        sid = bus.subscribe("click", lambda e: None)
        assert bus.unsubscribe(sid) is True

    def test_unsubscribe_nonexistent(self):
        bus = EventBus()
        assert bus.unsubscribe("nonexistent-id") is False

    def test_unsubscribe_removes_handler(self):
        bus = EventBus()
        calls = []
        sid = bus.subscribe("click", lambda e: calls.append(1))
        bus.unsubscribe(sid)
        bus.publish("click")
        assert calls == []

    def test_get_subscribers(self):
        bus = EventBus()
        handler = lambda e: None
        sid = bus.subscribe("click", handler, priority=5)
        subs = bus.get_subscribers("click")
        assert len(subs) == 1
        assert subs[0]["subscription_id"] == sid
        assert subs[0]["handler"] is handler
        assert subs[0]["priority"] == 5
        assert subs[0]["event_type"] == "click"

    def test_get_subscribers_empty(self):
        bus = EventBus()
        assert bus.get_subscribers("click") == []


class TestEventBusPublish:
    """Tests for publish."""

    def test_publish_calls_handler(self):
        bus = EventBus()
        received = []
        bus.subscribe("click", lambda e: received.append(e))
        count = bus.publish("click", {"x": 10})
        assert count == 1
        assert len(received) == 1
        assert received[0].event_type == "click"
        assert received[0].data == {"x": 10}

    def test_publish_returns_zero_no_subscribers(self):
        bus = EventBus()
        assert bus.publish("click") == 0

    def test_publish_multiple_handlers(self):
        bus = EventBus()
        calls = []
        bus.subscribe("click", lambda e: calls.append("a"))
        bus.subscribe("click", lambda e: calls.append("b"))
        count = bus.publish("click")
        assert count == 2
        assert len(calls) == 2

    def test_publish_priority_order(self):
        bus = EventBus()
        order = []
        bus.subscribe("click", lambda e: order.append("low"), priority=1)
        bus.subscribe("click", lambda e: order.append("high"), priority=10)
        bus.subscribe("click", lambda e: order.append("mid"), priority=5)
        bus.publish("click")
        assert order == ["high", "mid", "low"]

    def test_publish_stable_order_same_priority(self):
        bus = EventBus()
        order = []
        bus.subscribe("click", lambda e: order.append("first"), priority=0)
        bus.subscribe("click", lambda e: order.append("second"), priority=0)
        bus.subscribe("click", lambda e: order.append("third"), priority=0)
        bus.publish("click")
        assert order == ["first", "second", "third"]

    def test_publish_does_not_cross_event_types(self):
        bus = EventBus()
        calls = []
        bus.subscribe("click", lambda e: calls.append("click"))
        bus.subscribe("hover", lambda e: calls.append("hover"))
        bus.publish("click")
        assert calls == ["click"]


class TestStopPropagation:
    """Tests for stop_propagation."""

    def test_stop_propagation_prevents_subsequent(self):
        bus = EventBus()
        order = []

        def stopper(e):
            order.append("stopper")
            e.stop_propagation()

        bus.subscribe("click", stopper, priority=10)
        bus.subscribe("click", lambda e: order.append("after"), priority=1)
        count = bus.publish("click")
        assert order == ["stopper"]
        assert count == 1

    def test_stop_propagation_mid_chain(self):
        bus = EventBus()
        order = []

        bus.subscribe("click", lambda e: order.append("first"), priority=10)

        def stopper(e):
            order.append("stopper")
            e.stop_propagation()

        bus.subscribe("click", stopper, priority=5)
        bus.subscribe("click", lambda e: order.append("last"), priority=1)
        count = bus.publish("click")
        assert order == ["first", "stopper"]
        assert count == 2


class TestExceptionHandling:
    """Tests for handler exception isolation."""

    def test_exception_does_not_stop_others(self):
        bus = EventBus()
        calls = []

        def bad_handler(e):
            raise ValueError("boom")

        bus.subscribe("click", bad_handler, priority=10)
        bus.subscribe("click", lambda e: calls.append("ok"), priority=1)
        count = bus.publish("click")
        assert count == 2
        assert calls == ["ok"]

    def test_exception_counted_as_called(self):
        bus = EventBus()

        def bad_handler(e):
            raise RuntimeError("fail")

        bus.subscribe("click", bad_handler)
        count = bus.publish("click")
        assert count == 1


class TestPublishAsync:
    """Tests for publish_async."""

    def test_publish_async_returns_count(self):
        bus = EventBus()
        bus.subscribe("click", lambda e: None)
        bus.subscribe("click", lambda e: None)
        count = bus.publish_async("click")
        assert count == 2

    def test_publish_async_calls_handlers(self):
        bus = EventBus()
        results = []
        lock = threading.Lock()

        def handler(e):
            with lock:
                results.append(e.event_type)

        bus.subscribe("click", handler)
        bus.publish_async("click")
        time.sleep(0.2)
        assert results == ["click"]

    def test_publish_async_exception_isolated(self):
        bus = EventBus()
        results = []
        lock = threading.Lock()

        def bad(e):
            raise ValueError("boom")

        def good(e):
            with lock:
                results.append("ok")

        bus.subscribe("click", bad, priority=10)
        bus.subscribe("click", good, priority=1)
        count = bus.publish_async("click")
        time.sleep(0.2)
        assert count == 2
        assert results == ["ok"]


class TestClear:
    """Tests for clear."""

    def test_clear_specific_event_type(self):
        bus = EventBus()
        bus.subscribe("click", lambda e: None)
        bus.subscribe("hover", lambda e: None)
        bus.clear("click")
        assert bus.get_subscribers("click") == []
        assert len(bus.get_subscribers("hover")) == 1

    def test_clear_all(self):
        bus = EventBus()
        bus.subscribe("click", lambda e: None)
        bus.subscribe("hover", lambda e: None)
        bus.clear()
        assert bus.get_subscribers("click") == []
        assert bus.get_subscribers("hover") == []


class TestEventHandlerDecorator:
    """Tests for the event_handler decorator."""

    def test_decorator_sets_info(self):
        @event_handler("click", priority=5)
        def on_click(e):
            pass

        assert on_click._event_handler_info == {
            "event_type": "click",
            "priority": 5,
        }

    def test_decorator_auto_register(self):
        bus = EventBus()
        results = []

        @event_handler("click", priority=3)
        def on_click(e):
            results.append("clicked")

        # Auto-register using decorator info
        info = on_click._event_handler_info
        bus.subscribe(info["event_type"], on_click, info["priority"])

        bus.publish("click")
        assert results == ["clicked"]

    def test_decorator_default_priority(self):
        @event_handler("click")
        def on_click(e):
            pass

        assert on_click._event_handler_info["priority"] == 0


class TestThreadSafety:
    """Tests for thread-safe subscribe/unsubscribe."""

    def test_concurrent_subscribe(self):
        bus = EventBus()
        ids = []
        lock = threading.Lock()

        def subscribe_many():
            for _ in range(50):
                sid = bus.subscribe("click", lambda e: None)
                with lock:
                    ids.append(sid)

        threads = [threading.Thread(target=subscribe_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == 200
        assert len(set(ids)) == 200  # all unique

    def test_concurrent_subscribe_and_publish(self):
        bus = EventBus()
        count_holder = [0]
        lock = threading.Lock()

        def handler(e):
            with lock:
                count_holder[0] += 1

        for _ in range(10):
            bus.subscribe("click", handler)

        errors = []

        def publisher():
            try:
                for _ in range(20):
                    bus.publish("click")
            except Exception as ex:
                errors.append(ex)

        def subscriber():
            try:
                for _ in range(20):
                    bus.subscribe("click", handler)
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=publisher) for _ in range(3)]
        threads += [threading.Thread(target=subscriber) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_unsubscribe(self):
        bus = EventBus()
        sids = [bus.subscribe("click", lambda e: None) for _ in range(100)]
        errors = []

        def unsub(ids):
            try:
                for sid in ids:
                    bus.unsubscribe(sid)
            except Exception as ex:
                errors.append(ex)

        half = len(sids) // 2
        t1 = threading.Thread(target=unsub, args=(sids[:half],))
        t2 = threading.Thread(target=unsub, args=(sids[half:],))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == []
        assert bus.get_subscribers("click") == []


class TestEdgeCases:
    """Edge case tests."""

    def test_publish_with_no_event_type_subscribers(self):
        bus = EventBus()
        bus.subscribe("other", lambda e: None)
        assert bus.publish("click") == 0

    def test_clear_nonexistent_event_type(self):
        bus = EventBus()
        bus.clear("nonexistent")  # should not raise

    def test_subscribe_after_clear(self):
        bus = EventBus()
        bus.subscribe("click", lambda e: None)
        bus.clear("click")
        calls = []
        bus.subscribe("click", lambda e: calls.append(1))
        bus.publish("click")
        assert calls == [1]

    def test_multiple_unsubscribe_same_id(self):
        bus = EventBus()
        sid = bus.subscribe("click", lambda e: None)
        assert bus.unsubscribe(sid) is True
        assert bus.unsubscribe(sid) is False

    def test_publish_async_returns_immediately(self):
        bus = EventBus()
        barrier = threading.Event()

        def slow_handler(e):
            barrier.wait(timeout=2)

        bus.subscribe("click", slow_handler)
        start = time.time()
        count = bus.publish_async("click")
        elapsed = time.time() - start
        assert count == 1
        assert elapsed < 1.0  # should return almost immediately
        barrier.set()  # release the handler thread

    def test_handler_receives_event_object(self):
        bus = EventBus()
        received = []
        bus.subscribe("click", lambda e: received.append(e))
        bus.publish("click", {"x": 42})
        assert isinstance(received[0], Event)
        assert received[0].data["x"] == 42
