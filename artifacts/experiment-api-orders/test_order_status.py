"""Unit tests for order management module."""

import pytest
from app import create_order, get_order, update_order_status, list_orders, _orders


@pytest.fixture(autouse=True)
def clear_orders():
    """Reset order storage before each test."""
    _orders.clear()
    import app
    app._next_id = 1


def _make_order(customer="Alice", status_chain=None) -> dict:
    order = create_order(customer, [{"product": "Widget", "quantity": 1, "unit_price": 10.0}])
    for s in (status_chain or []):
        update_order_status(order["id"], s)
    return order


# ── create_order ─────────────────────────────────────────────

class TestCreateOrder:
    def test_creates_order_with_correct_fields(self):
        order = create_order("Bob", [{"product": "A", "quantity": 2, "unit_price": 5.0}])
        assert order["customer_name"] == "Bob"
        assert order["status"] == "pending"
        assert order["total"] == 10.0
        assert order["id"] == 1

    def test_auto_increments_id(self):
        o1 = _make_order()
        o2 = _make_order()
        assert o2["id"] == o1["id"] + 1

    def test_calculates_total_with_multiple_items(self):
        items = [
            {"product": "A", "quantity": 2, "unit_price": 3.5},
            {"product": "B", "quantity": 1, "unit_price": 4.0},
        ]
        order = create_order("Carol", items)
        assert order["total"] == 11.0

    def test_empty_items_raises(self):
        with pytest.raises(ValueError, match="items must not be empty"):
            create_order("Dan", [])


# ── get_order ────────────────────────────────────────────────

class TestGetOrder:
    def test_returns_existing_order(self):
        order = _make_order()
        assert get_order(order["id"]) == order

    def test_returns_none_for_missing_order(self):
        assert get_order(999) is None


# ── update_order_status ──────────────────────────────────────

class TestUpdateOrderStatusExists:
    def test_function_is_callable(self):
        assert callable(update_order_status)


class TestValidTransitions:
    def test_pending_to_confirmed(self):
        order = _make_order()
        result = update_order_status(order["id"], "confirmed")
        assert result["status"] == "confirmed"

    def test_confirmed_to_shipped(self):
        order = _make_order()
        update_order_status(order["id"], "confirmed")
        result = update_order_status(order["id"], "shipped")
        assert result["status"] == "shipped"

    def test_shipped_to_delivered(self):
        order = _make_order()
        update_order_status(order["id"], "confirmed")
        update_order_status(order["id"], "shipped")
        result = update_order_status(order["id"], "delivered")
        assert result["status"] == "delivered"


class TestCancelledFromAnyState:
    @pytest.mark.parametrize("steps,start_status", [
        ([], "pending"),
        (["confirmed"], "confirmed"),
        (["confirmed", "shipped"], "shipped"),
        (["confirmed", "shipped", "delivered"], "delivered"),
    ])
    def test_cancel_from_any_state(self, steps, start_status):
        order = _make_order()
        for step in steps:
            update_order_status(order["id"], step)
        assert order["status"] == start_status
        result = update_order_status(order["id"], "cancelled")
        assert result["status"] == "cancelled"


class TestInvalidTransitions:
    def test_pending_to_shipped_raises(self):
        order = _make_order()
        with pytest.raises(ValueError):
            update_order_status(order["id"], "shipped")

    def test_delivered_to_pending_raises(self):
        order = _make_order(status_chain=["confirmed", "shipped", "delivered"])
        with pytest.raises(ValueError):
            update_order_status(order["id"], "pending")

    def test_cancelled_to_pending_raises(self):
        order = _make_order()
        update_order_status(order["id"], "cancelled")
        with pytest.raises(ValueError):
            update_order_status(order["id"], "pending")


class TestOrderNotFound:
    def test_nonexistent_order_raises(self):
        with pytest.raises(ValueError, match="not found"):
            update_order_status(999, "confirmed")


# ── list_orders ──────────────────────────────────────────────

class TestListOrders:
    def test_returns_empty_list_when_no_orders(self):
        assert list_orders() == []

    def test_returns_all_orders(self):
        _make_order("Alice")
        _make_order("Bob")
        _make_order("Carol")
        result = list_orders()
        assert len(result) == 3

    def test_filter_by_pending_status(self):
        _make_order("Alice")  # pending
        _make_order("Bob", status_chain=["confirmed"])  # confirmed
        result = list_orders(status="pending")
        assert len(result) == 1
        assert result[0]["customer_name"] == "Alice"

    def test_filter_by_shipped_status(self):
        _make_order("Alice")  # pending
        _make_order("Bob", status_chain=["confirmed", "shipped"])  # shipped
        _make_order("Carol", status_chain=["confirmed"])  # confirmed
        result = list_orders(status="shipped")
        assert len(result) == 1
        assert result[0]["customer_name"] == "Bob"

    def test_filter_by_nonexistent_status_returns_empty(self):
        _make_order("Alice")
        assert list_orders(status="nonexistent") == []

    def test_filter_by_cancelled_status(self):
        _make_order("Alice")  # pending
        o2 = _make_order("Bob")
        update_order_status(o2["id"], "cancelled")
        result = list_orders(status="cancelled")
        assert len(result) == 1
        assert result[0]["customer_name"] == "Bob"

    def test_none_status_returns_all(self):
        _make_order("Alice")
        _make_order("Bob", status_chain=["confirmed"])
        result = list_orders(status=None)
        assert len(result) == 2
