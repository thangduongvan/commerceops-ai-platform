"""V6: product GETs use the read session; writes and orders stay on the primary.

Uses a second in-memory SQLite database that is deliberately *stale* relative
to the primary — the concrete demonstration of asynchronous replica lag as a
regression test. See docs/adr/ADR-007-database-ha.md.

IMPORTANT: do not `from tests.integration.conftest import ...`. Pytest loads
conftest as a plugin under a different module name; importing it again creates
a second in-memory engine and rewires dependency_overrides onto it, while
`_reset_database` keeps creating tables on the first engine — every
integration test then hits "no such table".
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import config as config_module
from app.core.database import Base, get_read_db
from app.product.main import app as product_app
from app.product.models import Product


@pytest.fixture
def stale_replica(client):
    """Seed primary via the API, then point get_read_db at a second DB that
    still has the *old* product name — simulating replica lag."""
    previous_cache = config_module.settings.cache_enabled
    config_module.settings.cache_enabled = False
    previous_override = product_app.dependency_overrides.get(get_read_db)

    created = client.post(
        "/products",
        json={"name": "Fresh Name", "price": 10.0, "stock_quantity": 5},
    )
    assert created.status_code == 201, created.text
    product_id = created.json()["id"]

    # Second engine = "replica". Copy the product row with a stale name.
    # Use sqlite:// (not :memory:) + StaticPool so the same connection — and
    # therefore the same schema — is shared across ReplicaSession checkouts.
    replica_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=replica_engine)
    ReplicaSession = sessionmaker(autocommit=False, autoflush=False, bind=replica_engine)
    with ReplicaSession() as session:
        session.add(
            Product(
                id=product_id,
                name="Stale Name",
                price=10.0,
                stock_quantity=5,
            )
        )
        session.commit()

    def _override_read():
        db = ReplicaSession()
        try:
            yield db
        finally:
            db.close()

    product_app.dependency_overrides[get_read_db] = _override_read

    try:
        yield product_id
    finally:
        config_module.settings.cache_enabled = previous_cache
        if previous_override is not None:
            product_app.dependency_overrides[get_read_db] = previous_override
        else:
            product_app.dependency_overrides.pop(get_read_db, None)
        replica_engine.dispose()


def test_product_get_serves_stale_replica_data(client, stale_replica):
    product_id = stale_replica
    body = client.get(f"/products/{product_id}").json()
    assert body["name"] == "Stale Name"


def test_product_list_serves_stale_replica_data(client, stale_replica):
    product_id = stale_replica
    names = {p["name"] for p in client.get("/products").json() if p["id"] == product_id}
    assert names == {"Stale Name"}


def test_product_put_reads_and_writes_primary(client, stale_replica):
    product_id = stale_replica
    updated = client.put(
        f"/products/{product_id}",
        json={"name": "Updated On Primary"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated On Primary"

    # GET still hits the stale replica override — proves the split is real.
    assert client.get(f"/products/{product_id}").json()["name"] == "Stale Name"


def test_order_path_sees_fresh_primary_stock(client, stale_replica, payment):
    """Orders must not use the replica: stock checks need the primary."""
    product_id = stale_replica
    payment.approves()

    customer = client.post(
        "/customers",
        json={"name": "Buyer", "email": "buyer-ha@example.com"},
    ).json()

    order = client.post(
        "/orders",
        json={
            "customer_id": customer["id"],
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    assert order.status_code == 201

    # Primary stock decremented; confirm via a primary-bound PUT response.
    primary_view = client.put(
        f"/products/{product_id}",
        json={"description": "touched"},
    ).json()
    assert primary_view["stock_quantity"] == 4
