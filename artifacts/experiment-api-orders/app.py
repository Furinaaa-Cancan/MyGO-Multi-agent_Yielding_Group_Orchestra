"""Order management module with in-memory storage."""

_orders: dict[int, dict] = {}
_next_id: int = 1

VALID_STATUSES = {"pending", "confirmed", "shipped", "delivered", "cancelled"}
STATUS_TRANSITIONS = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"shipped", "cancelled"},
    "shipped": {"delivered", "cancelled"},
    "delivered": {"cancelled"},
    "cancelled": set(),
}


def create_order(customer_name: str, items: list[dict]) -> dict:
    """Create a new order with the given customer name and items."""
    global _next_id

    if not items:
        raise ValueError("items cannot be empty")

    total = sum(item["quantity"] * item["unit_price"] for item in items)

    order = {
        "id": _next_id,
        "customer_name": customer_name,
        "items": items,
        "total": total,
        "status": "pending",
    }
    _orders[_next_id] = order
    _next_id += 1
    return order


def get_order(order_id: int) -> dict | None:
    """Get an order by its ID, or None if not found."""
    return _orders.get(order_id)


def update_order_status(order_id: int, status: str) -> dict:
    """Update the status of an order, enforcing valid transitions."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    order = _orders.get(order_id)
    if order is None:
        raise ValueError(f"Order {order_id} not found")

    current = order["status"]
    if status not in STATUS_TRANSITIONS[current]:
        raise ValueError(
            f"Cannot transition from '{current}' to '{status}'"
        )

    order["status"] = status
    return order


def list_orders(status: str | None = None) -> list[dict]:
    """List all orders, optionally filtered by status."""
    if status is None:
        return list(_orders.values())
    return [o for o in _orders.values() if o["status"] == status]
