"""V7 integration fixtures: one TestClient per service, shared in-memory SQLite.

Order no longer shares a DB with Product/Payment. Cross-service calls are
mocked at the client boundary (`app.clients.*`) so tests stay offline.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_read_db
from app.core import models as _core_models  # noqa: F401
from app.customer import models as _customer_models  # noqa: F401
from app.order import models as _order_models  # noqa: F401
from app.payment import models as _payment_models  # noqa: F401
from app.product import models as _product_models  # noqa: F401
from app.order.main import app as order_app
from app.payment.main import app as payment_app
from app.payment.schemas import PaymentResult
from app.product.main import app as product_app

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _override_get_read_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


for _app in (product_app, order_app, payment_app):
    _app.dependency_overrides[get_db] = _override_get_db
    _app.dependency_overrides[get_read_db] = _override_get_read_db


@pytest.fixture(autouse=True)
def _reset_database():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def product_client():
    return TestClient(product_app)


@pytest.fixture
def order_client():
    return TestClient(order_app)


@pytest.fixture
def payment_http_client():
    return TestClient(payment_app)


@pytest.fixture
def client(order_client, product_client, payment_http_client):
    """Composite client for tests that still hit mixed paths.

    Routes `/products` and `/internal` to the Product app, `/payments` to
    Payment, everything else to Order — mirrors the nginx/ALB gateway.
    """

    class GatewayClient:
        def request(self, method, url, **kwargs):
            path = url.split("?", 1)[0]
            if path.startswith("/products") or path.startswith("/internal"):
                return product_client.request(method, url, **kwargs)
            if path.startswith("/payments"):
                return payment_http_client.request(method, url, **kwargs)
            return order_client.request(method, url, **kwargs)

        def get(self, url, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url, **kwargs):
            return self.request("POST", url, **kwargs)

        def put(self, url, **kwargs):
            return self.request("PUT", url, **kwargs)

    return GatewayClient()


@pytest.fixture(autouse=True)
def wire_order_to_peers(monkeypatch, product_client, payment_http_client):
    """Order → Product/Payment over in-process HTTP (ASGI), not the network.

    Keeps the real client modules' call shape while avoiding Docker/DNS.
    """

    def _reserve(items):
        response = product_client.post("/internal/stock/reserve", json={"items": items})
        if response.status_code >= 400:
            from app.clients.product_client import ProductServiceError

            detail = response.json().get("detail", response.text)
            raise ProductServiceError(response.status_code, str(detail))
        return response.json()["items"]

    def _release(items):
        response = product_client.post("/internal/stock/release", json={"items": items})
        if response.status_code >= 400 and response.status_code != 204:
            from app.clients.product_client import ProductServiceError

            detail = response.json().get("detail", response.text) if response.content else response.text
            raise ProductServiceError(response.status_code, str(detail))

    def _charge(payload):
        response = payment_http_client.post(
            "/payments",
            json={"order_id": payload.order_id, "amount": payload.amount},
        )
        if response.status_code >= 400:
            return PaymentResult(
                order_id=payload.order_id,
                status="UNKNOWN",
                transaction_id="",
                amount=payload.amount,
                reason=f"http_{response.status_code}",
            )
        return PaymentResult.model_validate(response.json())

    monkeypatch.setattr("app.order.service.product_client.reserve", _reserve)
    monkeypatch.setattr("app.order.service.product_client.release", _release)
    monkeypatch.setattr("app.order.service.payment_client.charge", _charge)


@pytest.fixture
def payment(monkeypatch):
    """Steer the payment gateway outcome for Payment-service tests / order flow."""
    from app.payment import gateway_client

    class PaymentControl:
        def __init__(self) -> None:
            self.calls: list[tuple[int, float]] = []
            self._set(gateway_client.SUCCESS, "txn-test", "approved")

        def _set(self, status, transaction_id, reason):
            def _charge(order_id, amount):
                self.calls.append((order_id, amount))
                return gateway_client.GatewayOutcome(status, transaction_id, reason=reason)

            monkeypatch.setattr("app.payment.service.gateway_client.charge", _charge)

        def approves(self):
            self._set(gateway_client.SUCCESS, "txn-test", "approved")

        def declines(self):
            self._set(gateway_client.FAILED, "txn-declined", "declined")

        def never_answers(self, reason="retries_exhausted"):
            self._set(gateway_client.UNKNOWN, None, reason)

    return PaymentControl()
