"""Ground truth tests for task-api-orders: order management."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "artifacts" / "experiment-api-orders"))


def test_create_order():
    from app import create_order
    items = [{"product": "Widget", "quantity": 2, "unit_price": 10.0}]
    order = create_order("Alice", items)
    assert order["customer_name"] == "Alice"
    assert order["total"] == 20.0
    assert order["status"] == "pending"
    assert "id" in order


def test_create_order_total_calculation():
    from app import create_order
    items = [
        {"product": "A", "quantity": 3, "unit_price": 10.0},
        {"product": "B", "quantity": 1, "unit_price": 25.0},
    ]
    order = create_order("Bob", items)
    assert order["total"] == 55.0


def test_create_order_empty_items_raises():
    from app import create_order
    with pytest.raises(ValueError):
        create_order("Carol", [])


def test_get_order():
    from app import create_order, get_order
    items = [{"product": "X", "quantity": 1, "unit_price": 5.0}]
    order = create_order("Dave", items)
    found = get_order(order["id"])
    assert found is not None
    assert found["customer_name"] == "Dave"


def test_status_transition_valid():
    from app import create_order, update_order_status
    items = [{"product": "Y", "quantity": 1, "unit_price": 5.0}]
    order = create_order("Eve", items)
    updated = update_order_status(order["id"], "confirmed")
    assert updated["status"] == "confirmed"


def test_status_transition_invalid_raises():
    from app import create_order, update_order_status
    items = [{"product": "Z", "quantity": 1, "unit_price": 5.0}]
    order = create_order("Frank", items)
    # Cannot go from pending directly to shipped
    with pytest.raises(ValueError):
        update_order_status(order["id"], "shipped")


def test_cancel_from_any_status():
    from app import create_order, update_order_status
    items = [{"product": "W", "quantity": 1, "unit_price": 5.0}]
    order = create_order("Grace", items)
    updated = update_order_status(order["id"], "cancelled")
    assert updated["status"] == "cancelled"


def test_list_orders_filter_by_status():
    from app import create_order, list_orders
    items = [{"product": "V", "quantity": 1, "unit_price": 5.0}]
    create_order("H1", items)
    pending_orders = list_orders(status="pending")
    assert all(o["status"] == "pending" for o in pending_orders)
