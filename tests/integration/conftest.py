import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app

# Import models so all tables are registered on Base.metadata.
from app.core import models as _core_models  # noqa: F401
from app.customer import models as _customer_models  # noqa: F401
from app.order import models as _order_models  # noqa: F401
from app.product import models as _product_models  # noqa: F401

# In-memory SQLite shared across connections via StaticPool. This keeps
# integration tests fast and dependency-free on any OS; the Postgres
# container in docker-compose.yml remains the real deployment target.
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


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def _reset_database():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    # Not used as a context manager on purpose: it avoids triggering the
    # app's lifespan (which calls create_all against the real Postgres
    # engine), so tests never need a live database connection.
    return TestClient(app)


@pytest.fixture
def payment(monkeypatch):
    """Force the payment gateway's outcome, without any HTTP or a live gateway.

    V5 replaced the in-process `random.random()` payment stub with a real HTTP
    call, so tests can no longer steer the outcome by patching randomness. They
    patch the seam one level up instead: gateway_client.charge, which is where
    timeouts, retries, the breaker and the bulkhead have already been resolved
    into a single outcome. The client's own reliability behaviour is covered in
    tests/unit/test_payment_gateway_client.py; these tests only care about how
    the order flow reacts to each outcome.
    """
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
            """The gateway answered and said no — an unambiguous failure."""
            self._set(gateway_client.FAILED, "txn-declined", "declined")

        def never_answers(self, reason="retries_exhausted"):
            """Timeout / exhausted retries / open circuit / shed by the bulkhead.

            The charge may or may not have happened, which is what
            OrderStatus.PAYMENT_PENDING exists to represent.
            """
            self._set(gateway_client.UNKNOWN, None, reason)

    return PaymentControl()
