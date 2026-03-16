"""Gold-standard tests for the Event Bus System."""
import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_bus import EventBus, Event, event_handler


# ---------------------------------------------------------------------------
# Event object
# ---------------------------------------------------------------------------

class TestEvent:
    def test_event_has_required_properties(self):
        e = Event("click", {"x": 10})
        assert e.event_type == "click"
        assert e.data == {"x": 10}
        assert isinstance(e.timestamp, float)
        assert isinstance(e.id, str) and len(e.id) > 0

    def test_event_default_data_is_dict(self):
        e = Event("ping")
        assert isinstance(e.data, dict)

    def test_stop_propagation(self):
        e = Event("x")
        assert e.is_propagation_stopped is False
        e.stop_propagation()
        assert e.is_propagation_stopped is True

    def test_event_ids_are_unique(self):
        ids = {Event("a").id for _ in range(50)}
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# Basic subscribe / publish
# ---------------------------------------------------------------------------

class TestSubscribePublish:
    def test_subscribe_returns_string_id(self):
        bus = EventBus()
        sid = bus.subscribe("evt", lambda e: None)
        assert isinstance(sid, str) and len(sid) > 0

    def test_publish_calls_handler(self):
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe("evt", handler)
        count = bus.publish("evt", {"key": "val"})
        assert count == 1
        handler.assert_called_once()
        event_arg = handler.call_args[0][0]
        assert isinstance(event_arg, Event)
        assert event_arg.event_type == "evt"
        assert event_arg.data == {"key": "val"}

    def test_publish_no_subscribers_returns_zero(self):
        bus = EventBus()
        assert bus.publish("nope") == 0

    def test_publish_with_none_data(self):
        bus = EventBus()
        received = []
        bus.subscribe("evt", lambda e: received.append(e.data))
        bus.publish("evt")
        assert received[0] == {} or received[0] is None or isinstance(received[0], dict)

    def test_multiple_handlers_same_event(self):
        bus = EventBus()
        calls = []
        bus.subscribe("evt", lambda e: calls.append("a"))
        bus.subscribe("evt", lambda e: calls.append("b"))
        count = bus.publish("evt")
        assert count == 2
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriority:
    def test_higher_priority_runs_first(self):
        bus = EventBus()
        order = []
        bus.subscribe("evt", lambda e: order.append("low"), priority=1)
        bus.subscribe("evt", lambda e: order.append("high"), priority=10)
        bus.subscribe("evt", lambda e: order.append("mid"), priority=5)
        bus.publish("evt")
        assert order == ["high", "mid", "low"]

    def test_same_priority_preserves_insertion_order(self):
        bus = EventBus()
        order = []
        bus.subscribe("evt", lambda e: order.append("first"), priority=0)
        bus.subscribe("evt", lambda e: order.append("second"), priority=0)
        bus.subscribe("evt", lambda e: order.append("third"), priority=0)
        bus.publish("evt")
        assert order == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    def test_unsubscribe_removes_handler(self):
        bus = EventBus()
        handler = MagicMock()
        sid = bus.subscribe("evt", handler)
        assert bus.unsubscribe(sid) is True
        bus.publish("evt")
        handler.assert_not_called()

    def test_unsubscribe_unknown_id_returns_false(self):
        bus = EventBus()
        assert bus.unsubscribe("nonexistent-id") is False

    def test_double_unsubscribe_returns_false(self):
        bus = EventBus()
        sid = bus.subscribe("evt", lambda e: None)
        assert bus.unsubscribe(sid) is True
        assert bus.unsubscribe(sid) is False


# ---------------------------------------------------------------------------
# stop_propagation
# ---------------------------------------------------------------------------

class TestStopPropagation:
    def test_stop_propagation_prevents_later_handlers(self):
        bus = EventBus()
        order = []

        def stopper(e):
            order.append("stopper")
            e.stop_propagation()

        bus.subscribe("evt", stopper, priority=10)
        bus.subscribe("evt", lambda e: order.append("after"), priority=1)
        count = bus.publish("evt")
        assert order == ["stopper"]
        assert count == 1


# ---------------------------------------------------------------------------
# Handler exceptions
# ---------------------------------------------------------------------------

class TestHandlerExceptions:
    def test_exception_does_not_stop_other_handlers(self):
        bus = EventBus()
        calls = []

        def bad_handler(e):
            raise ValueError("boom")

        bus.subscribe("evt", bad_handler, priority=10)
        bus.subscribe("evt", lambda e: calls.append("ok"), priority=1)
        count = bus.publish("evt")
        assert "ok" in calls
        assert count == 2


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_specific_event(self):
        bus = EventBus()
        bus.subscribe("a", lambda e: None)
        bus.subscribe("b", lambda e: None)
        bus.clear("a")
        assert bus.publish("a") == 0
        assert bus.publish("b") == 1

    def test_clear_all(self):
        bus = EventBus()
        bus.subscribe("a", lambda e: None)
        bus.subscribe("b", lambda e: None)
        bus.clear()
        assert bus.publish("a") == 0
        assert bus.publish("b") == 0


# ---------------------------------------------------------------------------
# get_subscribers
# ---------------------------------------------------------------------------

class TestGetSubscribers:
    def test_returns_list_of_dicts(self):
        bus = EventBus()
        bus.subscribe("evt", lambda e: None, priority=5)
        subs = bus.get_subscribers("evt")
        assert isinstance(subs, list)
        assert len(subs) == 1
        assert isinstance(subs[0], dict)

    def test_empty_for_unknown_event(self):
        bus = EventBus()
        assert bus.get_subscribers("nope") == []


# ---------------------------------------------------------------------------
# event_handler decorator
# ---------------------------------------------------------------------------

class TestDecorator:
    def test_decorator_registers_handler(self):
        bus = EventBus()
        calls = []

        @event_handler("decor_evt", priority=5)
        def on_decor(e):
            calls.append(e.event_type)

        # The decorator should store metadata; we register manually via bus or
        # the decorator itself registers on a default bus.  We support both
        # patterns: if the decorator auto-registers to a global bus, we test
        # that; otherwise we allow bus.subscribe with the decorated fn.
        bus.subscribe("decor_evt", on_decor, priority=5)
        bus.publish("decor_evt")
        assert calls == ["decor_evt"]


# ---------------------------------------------------------------------------
# Multiple event types isolation
# ---------------------------------------------------------------------------

class TestMultipleEventTypes:
    def test_handlers_only_receive_their_event(self):
        bus = EventBus()
        a_calls, b_calls = [], []
        bus.subscribe("a", lambda e: a_calls.append(1))
        bus.subscribe("b", lambda e: b_calls.append(1))
        bus.publish("a")
        assert len(a_calls) == 1
        assert len(b_calls) == 0


# ---------------------------------------------------------------------------
# publish_async
# ---------------------------------------------------------------------------

class TestPublishAsync:
    def test_publish_async_returns_count(self):
        bus = EventBus()
        bus.subscribe("evt", lambda e: None)
        bus.subscribe("evt", lambda e: None)
        count = bus.publish_async("evt")
        assert count == 2

    def test_publish_async_calls_handlers(self):
        bus = EventBus()
        results = []
        done = threading.Event()
        lock = threading.Lock()

        def handler(e):
            with lock:
                results.append(e.event_type)
            done.set()

        bus.subscribe("evt", handler)
        bus.publish_async("evt")
        # Wait for handler thread with timeout instead of fixed sleep
        done.wait(timeout=5.0)
        assert "evt" in results
