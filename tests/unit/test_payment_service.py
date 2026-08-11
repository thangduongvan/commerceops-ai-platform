"""V5/V7: payment service maps gateway outcomes and persists idempotent rows."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.payment import gateway_client, models as _payment_models  # noqa: F401
from app.payment import service
from app.payment.schemas import PaymentRequest


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[_payment_models.Payment.__table__])
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[_payment_models.Payment.__table__])


@pytest.fixture
def outcome(monkeypatch):
    def _set(status, transaction_id=None, reason=""):
        monkeypatch.setattr(
            "app.payment.service.gateway_client.charge",
            lambda order_id, amount: gateway_client.GatewayOutcome(
                status, transaction_id, reason=reason
            ),
        )

    return _set


def test_an_approved_charge_maps_to_success(outcome, db):
    outcome(gateway_client.SUCCESS, "txn-1", "approved")

    result = service.charge(db, PaymentRequest(order_id=1, amount=100.0))

    assert result.status == "SUCCESS"
    assert result.transaction_id == "txn-1"
    assert result.order_id == 1
    assert result.amount == 100.0


def test_a_declined_charge_maps_to_failed(outcome, db):
    outcome(gateway_client.FAILED, "txn-2", "declined")

    result = service.charge(db, PaymentRequest(order_id=2, amount=50.0))

    assert result.status == "FAILED"
    assert result.reason == "declined"


def test_no_answer_maps_to_unknown_and_not_to_failed(outcome, db):
    outcome(gateway_client.UNKNOWN, None, "retries_exhausted")

    result = service.charge(db, PaymentRequest(order_id=3, amount=10.0))

    assert result.status == "UNKNOWN"
    assert result.reason == "retries_exhausted"


def test_an_unknown_outcome_still_carries_a_transaction_id_placeholder(outcome, db):
    outcome(gateway_client.UNKNOWN, None, "timeout")

    result = service.charge(db, PaymentRequest(order_id=4, amount=10.0))

    assert result.transaction_id
    assert result.status == "UNKNOWN"


def test_the_order_amount_is_passed_through_to_the_gateway(monkeypatch, db):
    calls = []

    def _charge(order_id, amount):
        calls.append((order_id, amount))
        return gateway_client.GatewayOutcome(
            gateway_client.SUCCESS, "txn", reason="approved"
        )

    monkeypatch.setattr("app.payment.service.gateway_client.charge", _charge)

    service.charge(db, PaymentRequest(order_id=9, amount=42.5))

    assert calls == [(9, 42.5)]


def test_charge_is_idempotent_per_order_id(outcome, db):
    outcome(gateway_client.SUCCESS, "txn-1", "approved")
    first = service.charge(db, PaymentRequest(order_id=11, amount=10.0))

    outcome(gateway_client.FAILED, "txn-2", "declined")
    second = service.charge(db, PaymentRequest(order_id=11, amount=10.0))

    assert first.status == "SUCCESS"
    assert second.status == "SUCCESS"
    assert second.transaction_id == first.transaction_id
