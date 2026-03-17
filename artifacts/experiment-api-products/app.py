"""Product CRUD module with in-memory storage."""

products: dict[int, dict] = {}
_next_id: int = 1


def create_product(name: str, price: float, stock: int = 0) -> dict:
    """Create a product and return it as a dict with auto-incremented id."""
    global _next_id
    if price <= 0:
        raise ValueError("price must be positive")
    product = {"id": _next_id, "name": name, "price": price, "stock": stock}
    products[_next_id] = product
    _next_id += 1
    return product


def get_product(product_id: int) -> dict | None:
    """Return product by id, or None if not found."""
    return products.get(product_id)


def list_products(
    min_price: float | None = None, max_price: float | None = None
) -> list[dict]:
    """Return all products, optionally filtered by price range [min_price, max_price]."""
    result = list(products.values())
    if min_price is not None:
        result = [p for p in result if p["price"] >= min_price]
    if max_price is not None:
        result = [p for p in result if p["price"] <= max_price]
    return result


def update_stock(product_id: int, delta: int) -> dict:
    """Adjust stock by delta (can be negative). Raises ValueError if stock would go below 0.
    Raises KeyError if product_id not found."""
    product = products.get(product_id)
    if product is None:
        raise KeyError(f"Product {product_id} not found")
    new_stock = product["stock"] + delta
    if new_stock < 0:
        raise ValueError("Stock cannot be negative")
    product["stock"] = new_stock
    return product
