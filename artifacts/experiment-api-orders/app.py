# Order management module with in-memory storage

_orders: dict[int, dict] = {}
_next_id: int = 1


def create_order(customer_name: str, items: list[dict]) -> dict:
    """Create a new order with auto-calculated total.

    Args:
        customer_name: Name of the customer.
        items: List of dicts with 'product', 'quantity', and 'unit_price'.

    Returns:
        Order dict with id, customer_name, items, total, and status.

    Raises:
        ValueError: If items is empty.
    """
    global _next_id

    if not items:
        raise ValueError("items must not be empty")

    total = sum(item["quantity"] * item["unit_price"] for item in items)

    order = {
        "id": _next_id,
        "customer_name": customer_name,
        "items": items,
        "total": round(total, 2),
        "status": "pending",
    }

    _orders[_next_id] = order
    _next_id += 1

    return order


def get_order(order_id: int) -> dict | None:
    """Retrieve an order by ID.

    Returns:
        The order dict, or None if not found.
    """
    return _orders.get(order_id)


# Valid state transitions: current_status -> set of allowed next statuses
_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"shipped", "cancelled"},
    "shipped": {"delivered", "cancelled"},
    "delivered": {"cancelled"},
    "cancelled": set(),
}


def update_order_status(order_id: int, new_status: str) -> dict:
    """Update the status of an order following the state machine rules.

    Allowed transitions:
        pending -> confirmed -> shipped -> delivered
        Any state -> cancelled

    Args:
        order_id: The ID of the order to update.
        new_status: The desired new status.

    Returns:
        The updated order dict.

    Raises:
        ValueError: If the order is not found or the transition is invalid.
    """
    order = _orders.get(order_id)
    if order is None:
        raise ValueError(f"Order {order_id} not found")

    current = order["status"]
    allowed = _TRANSITIONS.get(current, set())

    if new_status not in allowed:
        raise ValueError(
            f"Invalid status transition: {current} -> {new_status}"
        )

    order["status"] = new_status
    return order


def list_orders(status: str | None = None) -> list[dict]:
    """Return all orders, optionally filtered by status.

    Args:
        status: If provided, only return orders with this status.

    Returns:
        List of order dicts matching the criteria.
    """
    if status is None:
        return list(_orders.values())
    return [o for o in _orders.values() if o["status"] == status]
