"""Product management module with in-memory storage."""

_products: dict[int, dict] = {}
_next_id: int = 1


def create_product(name: str, price: float, stock: int = 0) -> dict:
    """Create a new product. Price must be > 0."""
    global _next_id
    if price <= 0:
        raise ValueError("price must be greater than 0")
    product = {"id": _next_id, "name": name, "price": price, "stock": stock}
    _products[_next_id] = product
    _next_id += 1
    return dict(product)


def get_product(product_id: int) -> dict | None:
    """Get a product by ID, or None if not found."""
    product = _products.get(product_id)
    return dict(product) if product is not None else None


def list_products(min_price: float = 0, max_price: float = float("inf")) -> list[dict]:
    """List products filtered by price range."""
    return [
        dict(p)
        for p in _products.values()
        if min_price <= p["price"] <= max_price
    ]


def update_stock(product_id: int, delta: int) -> dict:
    """Update stock by delta. Stock cannot go below 0."""
    product = _products.get(product_id)
    if product is None:
        raise ValueError(f"product {product_id} not found")
    new_stock = product["stock"] + delta
    if new_stock < 0:
        raise ValueError("stock cannot be negative")
    product["stock"] = new_stock
    return dict(product)
