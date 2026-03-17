"""Order management API — in-memory with status state machine."""
from __future__ import annotations

_orders: dict[int, dict] = {}
_next_id: int = 1

_VALID_STATUSES = {"pending", "confirmed", "shipped", "delivered", "cancelled"}
_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"shipped", "cancelled"},
    "shipped": {"delivered", "cancelled"},
    "delivered": {"cancelled"},
    "cancelled": set(),
}


def create_order(customer_name: str, items: list[dict]) -> dict:
    """Create a new order. Items must not be empty."""
    global _next_id
    if not items:
        raise ValueError("items must not be empty")
    total = sum(item["quantity"] * item["unit_price"] for item in items)
    order = {
        "id": _next_id,
        "customer_name": customer_name,
        "items": list(items),
        "total": total,
        "status": "pending",
    }
    _orders[_next_id] = order
    _next_id += 1
    return dict(order)


def get_order(order_id: int) -> dict | None:
    """Get order by ID."""
    o = _orders.get(order_id)
    return dict(o) if o else None


def update_order_status(order_id: int, status: str) -> dict:
    """Update order status following the state machine rules."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")
    o = _orders.get(order_id)
    if o is None:
        raise ValueError(f"Order {order_id} not found")
    current = o["status"]
    if status not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"Cannot transition from {current!r} to {status!r}")
    o["status"] = status
    return dict(o)


def list_orders(status: str | None = None) -> list[dict]:
    """List orders, optionally filtered by status."""
    if status is None:
        return [dict(o) for o in _orders.values()]
    return [dict(o) for o in _orders.values() if o["status"] == status]
