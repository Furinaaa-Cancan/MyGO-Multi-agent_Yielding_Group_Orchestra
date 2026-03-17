"""Ground truth tests for task-api-products: product management."""
import sys
from pathlib import Path
import importlib
import pytest

_artifact_path = str(Path(__file__).resolve().parents[3] / "artifacts" / "experiment-api-products")
if _artifact_path not in sys.path:
    sys.path.insert(0, _artifact_path)


@pytest.fixture(autouse=True)
def _reset_module_state():
    import app
    importlib.reload(app)


def test_create_product_returns_dict():
    from app import create_product
    p = create_product("Widget", 9.99, stock=10)
    assert isinstance(p, dict)
    assert p["name"] == "Widget"
    assert p["price"] == 9.99
    assert p["stock"] == 10
    assert "id" in p


def test_create_product_negative_price_raises():
    from app import create_product
    with pytest.raises(ValueError):
        create_product("Bad", -1.0)


def test_create_product_zero_price_raises():
    from app import create_product
    with pytest.raises(ValueError):
        create_product("Free", 0.0)


def test_get_product():
    from app import create_product, get_product
    p = create_product("Gadget", 19.99)
    found = get_product(p["id"])
    assert found is not None
    assert found["name"] == "Gadget"


def test_list_products_price_filter():
    from app import create_product, list_products
    create_product("Cheap", 5.0)
    create_product("Expensive", 500.0)
    filtered = list_products(min_price=100)
    assert all(p["price"] >= 100 for p in filtered)


def test_update_stock_increase():
    from app import create_product, update_stock
    p = create_product("Item", 10.0, stock=5)
    updated = update_stock(p["id"], 3)
    assert updated["stock"] == 8


def test_update_stock_decrease():
    from app import create_product, update_stock
    p = create_product("Item2", 10.0, stock=5)
    updated = update_stock(p["id"], -3)
    assert updated["stock"] == 2


def test_update_stock_below_zero_raises():
    from app import create_product, update_stock
    p = create_product("Item3", 10.0, stock=2)
    with pytest.raises(ValueError):
        update_stock(p["id"], -5)
