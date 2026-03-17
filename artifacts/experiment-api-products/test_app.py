"""Unit tests for product CRUD including list_products and update_stock."""

import pytest

import app


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state before each test."""
    app.products.clear()
    app._next_id = 1


# --- list_products ---

def test_list_products_returns_all():
    app.create_product("A", 5.0, stock=10)
    app.create_product("B", 15.0, stock=20)
    app.create_product("C", 25.0, stock=30)
    assert len(app.list_products()) == 3


def test_list_products_empty():
    assert app.list_products() == []


def test_list_products_min_price():
    app.create_product("Cheap", 5.0)
    app.create_product("Expensive", 50.0)
    result = app.list_products(min_price=10)
    assert len(result) == 1
    assert result[0]["name"] == "Expensive"


def test_list_products_max_price():
    app.create_product("Cheap", 5.0)
    app.create_product("Expensive", 50.0)
    result = app.list_products(max_price=10)
    assert len(result) == 1
    assert result[0]["name"] == "Cheap"


def test_list_products_price_range():
    app.create_product("A", 5.0)
    app.create_product("B", 15.0)
    app.create_product("C", 25.0)
    result = app.list_products(min_price=10, max_price=20)
    assert len(result) == 1
    assert result[0]["name"] == "B"


def test_list_products_inclusive_bounds():
    app.create_product("Exact", 10.0)
    assert len(app.list_products(min_price=10, max_price=10)) == 1


# --- update_stock ---

def test_update_stock_increase():
    p = app.create_product("Widget", 10.0, stock=5)
    updated = app.update_stock(p["id"], 10)
    assert updated["stock"] == 15


def test_update_stock_decrease():
    p = app.create_product("Widget", 10.0, stock=10)
    updated = app.update_stock(p["id"], -5)
    assert updated["stock"] == 5


def test_update_stock_to_zero():
    p = app.create_product("Widget", 10.0, stock=5)
    updated = app.update_stock(p["id"], -5)
    assert updated["stock"] == 0


def test_update_stock_negative_raises():
    p = app.create_product("Widget", 10.0, stock=3)
    with pytest.raises(ValueError):
        app.update_stock(p["id"], -9999)


def test_update_stock_not_found():
    with pytest.raises(KeyError):
        app.update_stock(999, 10)


def test_update_stock_returns_product_dict():
    p = app.create_product("Widget", 10.0, stock=5)
    result = app.update_stock(p["id"], 1)
    assert isinstance(result, dict)
    assert "id" in result
    assert "name" in result
    assert "stock" in result
