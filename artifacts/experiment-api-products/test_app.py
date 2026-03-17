"""Unit tests for product management module."""

import pytest
from app import create_product, get_product, list_products, update_stock, _products, _next_id
import app


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module state before each test."""
    app._products.clear()
    app._next_id = 1
    yield


class TestCreateProduct:
    def test_basic_creation(self):
        p = create_product("Widget", 9.99)
        assert p == {"id": 1, "name": "Widget", "price": 9.99, "stock": 0}

    def test_auto_increment_id(self):
        p1 = create_product("A", 1.0)
        p2 = create_product("B", 2.0)
        assert p1["id"] == 1
        assert p2["id"] == 2

    def test_custom_stock(self):
        p = create_product("C", 5.0, stock=10)
        assert p["stock"] == 10

    def test_zero_price_raises(self):
        with pytest.raises(ValueError):
            create_product("Bad", 0)

    def test_negative_price_raises(self):
        with pytest.raises(ValueError):
            create_product("Bad", -5.0)


class TestGetProduct:
    def test_existing(self):
        create_product("X", 1.0)
        assert get_product(1) is not None
        assert get_product(1)["name"] == "X"

    def test_not_found(self):
        assert get_product(999) is None

    def test_returns_copy(self):
        create_product("X", 1.0)
        p = get_product(1)
        p["name"] = "modified"
        assert get_product(1)["name"] == "X"


class TestListProducts:
    def test_empty(self):
        assert list_products() == []

    def test_all_products(self):
        create_product("A", 10.0)
        create_product("B", 20.0)
        assert len(list_products()) == 2

    def test_price_filter(self):
        create_product("Cheap", 5.0)
        create_product("Mid", 15.0)
        create_product("Expensive", 50.0)
        result = list_products(min_price=10.0, max_price=20.0)
        assert len(result) == 1
        assert result[0]["name"] == "Mid"

    def test_min_price_only(self):
        create_product("A", 5.0)
        create_product("B", 15.0)
        assert len(list_products(min_price=10.0)) == 1


class TestUpdateStock:
    def test_increase(self):
        create_product("X", 1.0, stock=5)
        p = update_stock(1, 3)
        assert p["stock"] == 8

    def test_decrease(self):
        create_product("X", 1.0, stock=5)
        p = update_stock(1, -3)
        assert p["stock"] == 2

    def test_decrease_to_zero(self):
        create_product("X", 1.0, stock=5)
        p = update_stock(1, -5)
        assert p["stock"] == 0

    def test_negative_stock_raises(self):
        create_product("X", 1.0, stock=2)
        with pytest.raises(ValueError):
            update_stock(1, -3)

    def test_not_found_raises(self):
        with pytest.raises(ValueError):
            update_stock(999, 1)

    def test_returns_copy(self):
        create_product("X", 1.0, stock=5)
        p = update_stock(1, 1)
        p["stock"] = 999
        assert get_product(1)["stock"] == 6
