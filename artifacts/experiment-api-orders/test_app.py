"""Unit tests for the order management module."""

import pytest
from app import create_order, get_order, update_order_status, list_orders, _orders, _next_id
import app


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module state before each test."""
    app._orders.clear()
    app._next_id = 1
    yield


class TestCreateOrder:
    def test_basic_creation(self):
        order = create_order("Alice", [{"product": "Widget", "quantity": 2, "unit_price": 9.99}])
        assert order["id"] == 1
        assert order["customer_name"] == "Alice"
        assert order["status"] == "pending"
        assert order["total"] == pytest.approx(19.98)

    def test_auto_increment_id(self):
        o1 = create_order("Alice", [{"product": "A", "quantity": 1, "unit_price": 1.0}])
        o2 = create_order("Bob", [{"product": "B", "quantity": 1, "unit_price": 2.0}])
        assert o1["id"] == 1
        assert o2["id"] == 2

    def test_total_calculation_multiple_items(self):
        items = [
            {"product": "A", "quantity": 2, "unit_price": 10.0},
            {"product": "B", "quantity": 3, "unit_price": 5.0},
        ]
        order = create_order("Alice", items)
        assert order["total"] == pytest.approx(35.0)

    def test_empty_items_raises(self):
        with pytest.raises(ValueError):
            create_order("Alice", [])


class TestGetOrder:
    def test_existing_order(self):
        created = create_order("Alice", [{"product": "X", "quantity": 1, "unit_price": 5.0}])
        found = get_order(created["id"])
        assert found == created

    def test_nonexistent_order(self):
        assert get_order(999) is None


class TestUpdateOrderStatus:
    def test_valid_transitions(self):
        order = create_order("Alice", [{"product": "X", "quantity": 1, "unit_price": 5.0}])
        oid = order["id"]

        updated = update_order_status(oid, "confirmed")
        assert updated["status"] == "confirmed"

        updated = update_order_status(oid, "shipped")
        assert updated["status"] == "shipped"

        updated = update_order_status(oid, "delivered")
        assert updated["status"] == "delivered"

    def test_cancel_from_any_non_cancelled(self):
        for start_status in ["pending", "confirmed", "shipped", "delivered"]:
            app._orders.clear()
            app._next_id = 1
            order = create_order("A", [{"product": "X", "quantity": 1, "unit_price": 1.0}])
            # Advance to start_status
            transitions = {
                "pending": [],
                "confirmed": ["confirmed"],
                "shipped": ["confirmed", "shipped"],
                "delivered": ["confirmed", "shipped", "delivered"],
            }
            for s in transitions[start_status]:
                update_order_status(order["id"], s)
            updated = update_order_status(order["id"], "cancelled")
            assert updated["status"] == "cancelled"

    def test_invalid_transition(self):
        order = create_order("Alice", [{"product": "X", "quantity": 1, "unit_price": 5.0}])
        with pytest.raises(ValueError):
            update_order_status(order["id"], "shipped")  # pending -> shipped invalid

    def test_invalid_status(self):
        order = create_order("Alice", [{"product": "X", "quantity": 1, "unit_price": 5.0}])
        with pytest.raises(ValueError):
            update_order_status(order["id"], "bogus")

    def test_nonexistent_order(self):
        with pytest.raises(ValueError):
            update_order_status(999, "confirmed")

    def test_cancelled_cannot_transition(self):
        order = create_order("Alice", [{"product": "X", "quantity": 1, "unit_price": 5.0}])
        update_order_status(order["id"], "cancelled")
        with pytest.raises(ValueError):
            update_order_status(order["id"], "pending")


class TestListOrders:
    def test_empty(self):
        assert list_orders() == []

    def test_all_orders(self):
        create_order("A", [{"product": "X", "quantity": 1, "unit_price": 1.0}])
        create_order("B", [{"product": "Y", "quantity": 1, "unit_price": 2.0}])
        assert len(list_orders()) == 2

    def test_filter_by_status(self):
        create_order("A", [{"product": "X", "quantity": 1, "unit_price": 1.0}])
        o2 = create_order("B", [{"product": "Y", "quantity": 1, "unit_price": 2.0}])
        update_order_status(o2["id"], "confirmed")
        pending = list_orders(status="pending")
        confirmed = list_orders(status="confirmed")
        assert len(pending) == 1
        assert len(confirmed) == 1
        assert pending[0]["customer_name"] == "A"
        assert confirmed[0]["customer_name"] == "B"
