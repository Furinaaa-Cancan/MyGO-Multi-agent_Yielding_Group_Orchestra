products = {}
_next_id = 1


def create_product(name, price, stock=0):
    global _next_id
    if price <= 0:
        raise ValueError("price must be greater than 0")
    product = {"id": _next_id, "name": name, "price": price, "stock": stock}
    products[_next_id] = product
    _next_id += 1
    return product


def get_product(product_id):
    return products.get(product_id)


def list_products(min_price=None, max_price=None):
    result = list(products.values())
    if min_price is not None:
        result = [p for p in result if p["price"] >= min_price]
    if max_price is not None:
        result = [p for p in result if p["price"] <= max_price]
    return result


def update_stock(product_id, delta):
    product = products.get(product_id)
    if product is None:
        raise KeyError(f"Product {product_id} not found")
    new_stock = product["stock"] + delta
    if new_stock < 0:
        raise ValueError("Stock cannot be negative")
    product["stock"] = new_stock
    return product
