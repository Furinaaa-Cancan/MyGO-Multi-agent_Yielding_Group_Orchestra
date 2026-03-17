"""Product management API — in-memory CRUD with stock management."""
from __future__ import annotations

_products: dict[int, dict] = {}
_next_id: int = 1


def create_product(name: str, price: float, stock: int = 0) -> dict:
    """Create a new product. Price must be > 0."""
    global _next_id
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")
    product = {"id": _next_id, "name": name, "price": price, "stock": stock}
    _products[_next_id] = product
    _next_id += 1
    return dict(product)


def get_product(product_id: int) -> dict | None:
    """Get product by ID."""
    p = _products.get(product_id)
    return dict(p) if p else None


def list_products(min_price: float = 0, max_price: float = float("inf")) -> list[dict]:
    """List products, optionally filtered by price range."""
    return [dict(p) for p in _products.values() if min_price <= p["price"] <= max_price]


def update_stock(product_id: int, delta: int) -> dict:
    """Update stock by delta. Raises ValueError if result would be negative."""
    p = _products.get(product_id)
    if p is None:
        raise ValueError(f"Product {product_id} not found")
    new_stock = p["stock"] + delta
    if new_stock < 0:
        raise ValueError(f"Stock cannot go below 0 (current={p['stock']}, delta={delta})")
    p["stock"] = new_stock
    return dict(p)
