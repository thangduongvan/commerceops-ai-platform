"""V3 (Caching): verifies the cache-aside wiring in app/product/service.py
without needing a real Redis -- the three cache helpers imported into that
module are monkeypatched with an in-memory fake, so these tests exercise
the actual service logic (which key is read/written/deleted, and when)
against the existing SQLite-backed `client` fixture from conftest.py.
"""

import pytest

from app.product import service as product_service


class FakeCache:
    def __init__(self):
        self.store: dict[str, object] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl_seconds):
        self.store[key] = value

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


@pytest.fixture
def fake_cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(product_service, "cache_get_json", fake.get)
    monkeypatch.setattr(product_service, "cache_set_json", fake.set)
    monkeypatch.setattr(product_service, "cache_delete", fake.delete)
    return fake


def _create_product(client, **overrides):
    payload = {"name": "Widget", "description": "A widget", "price": 9.99, "stock_quantity": 10}
    payload.update(overrides)
    resp = client.post("/products", json=payload)
    assert resp.status_code == 201
    return resp.json()


def test_get_product_populates_cache_on_miss(client, fake_cache):
    product = _create_product(client)
    cache_key = f"product:{product['id']}"
    assert cache_key not in fake_cache.store

    resp = client.get(f"/products/{product['id']}")
    assert resp.status_code == 200
    assert cache_key in fake_cache.store
    assert fake_cache.store[cache_key]["price"] == product["price"]


def test_get_product_serves_from_cache_on_hit(client, fake_cache):
    product = _create_product(client)
    cache_key = f"product:{product['id']}"

    # Seed the cache directly with a value that differs from the DB, so a
    # cache hit is unambiguous: if the response reflects this value (rather
    # than the real DB row), the DB was never consulted.
    fake_cache.store[cache_key] = {**product, "price": 1.23}

    resp = client.get(f"/products/{product['id']}")
    assert resp.status_code == 200
    assert resp.json()["price"] == 1.23


def test_list_products_populates_list_cache_key(client, fake_cache):
    _create_product(client)
    cache_key = "products:list:0:50"
    assert cache_key not in fake_cache.store

    resp = client.get("/products")
    assert resp.status_code == 200
    assert cache_key in fake_cache.store
    assert len(fake_cache.store[cache_key]) == 1


def test_update_product_invalidates_detail_cache(client, fake_cache):
    product = _create_product(client)
    cache_key = f"product:{product['id']}"

    client.get(f"/products/{product['id']}")  # populate the cache
    assert cache_key in fake_cache.store

    update_resp = client.put(f"/products/{product['id']}", json={"price": 12.5})
    assert update_resp.status_code == 200
    assert cache_key not in fake_cache.store

    # Next read is guaranteed fresh, not served from a stale cached copy.
    get_resp = client.get(f"/products/{product['id']}")
    assert get_resp.json()["price"] == 12.5
