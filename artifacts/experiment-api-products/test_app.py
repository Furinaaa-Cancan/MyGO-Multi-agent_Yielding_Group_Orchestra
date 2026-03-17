import pytest
from app import create_product, get_product, list_products, update_stock, products, _next_id
import app


def setup_function():
    products.clear()
    app._next_id = 1


def test_create_product_basic():
    result = create_product("Widget", 9.99)
    assert result == {"id": 1, "name": "Widget", "price": 9.99, "stock": 0}


def test_create_product_with_stock():
    create_product("Widget", 9.99)
    result = create_product("Gadget", 19.99, stock=5)
    assert result["id"] == 2
    assert result["name"] == "Gadget"
    assert result["price"] == 19.99
    assert result["stock"] == 5


def test_create_product_negative_price():
    with pytest.raises(ValueError):
        create_product("Bad", -1)


def test_create_product_zero_price():
    with pytest.raises(ValueError):
        create_product("Bad", 0)


def test_get_product_exists():
    create_product("Widget", 9.99)
    result = get_product(1)
    assert result == {"id": 1, "name": "Widget", "price": 9.99, "stock": 0}


def test_get_product_not_exists():
    assert get_product(999) is None


def test_id_auto_increment():
    p1 = create_product("A", 1.0)
    p2 = create_product("B", 2.0)
    p3 = create_product("C", 3.0)
    assert p1["id"] == 1
    assert p2["id"] == 2
    assert p3["id"] == 3


def test_list_products_all():
    create_product("A", 5.0)
    create_product("B", 15.0)
    create_product("C", 25.0)
    result = list_products()
    assert len(result) == 3


def test_list_products_min_price():
    create_product("A", 5.0)
    create_product("B", 15.0)
    create_product("C", 25.0)
    result = list_products(min_price=10)
    assert len(result) == 2
    assert all(p["price"] >= 10 for p in result)


def test_list_products_max_price():
    create_product("A", 5.0)
    create_product("B", 15.0)
    create_product("C", 25.0)
    result = list_products(max_price=20)
    assert len(result) == 2
    assert all(p["price"] <= 20 for p in result)


def test_list_products_price_range():
    create_product("A", 5.0)
    create_product("B", 15.0)
    create_product("C", 25.0)
    result = list_products(min_price=10, max_price=20)
    assert len(result) == 1
    assert result[0]["name"] == "B"


def test_list_products_empty():
    result = list_products()
    assert result == []


def test_update_stock_increase():
    create_product("A", 10.0, stock=5)
    result = update_stock(1, 10)
    assert result["stock"] == 15


def test_update_stock_decrease():
    create_product("A", 10.0, stock=10)
    result = update_stock(1, -5)
    assert result["stock"] == 5


def test_update_stock_to_zero():
    create_product("A", 10.0, stock=5)
    result = update_stock(1, -5)
    assert result["stock"] == 0


def test_update_stock_negative_raises():
    create_product("A", 10.0, stock=5)
    with pytest.raises(ValueError):
        update_stock(1, -9999)


def test_update_stock_not_found():
    with pytest.raises(KeyError):
        update_stock(999, 10)
