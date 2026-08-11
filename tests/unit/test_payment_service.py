"""V5 (Reliability): app/payment/service.py's mapping of gateway outcomes.

Thin by design. V5 moved everything interesting (timeouts, the retry ladder, the
circuit breaker, the bulkhead) down into app/payment/gateway_client.py, tested
in test_payment_gateway_client.py. What's left here is the translation from a
GatewayOutcome to a PaymentResult, and the one thing that translation must never
do is flatten UNKNOWN into FAILED.
"""

import pytest

from app.payment import gateway_client, service
from app.payment.schemas import PaymentRequest


@pytest.fixture
def outcome(monkeypatch):
    def _set(status, transaction_id=None, reason=""):
        monkeypatch.setattr(
            "app.payment.service.gateway_client.charge",
            lambda order_id, amount: gateway_client.GatewayOutcome(status, transaction_id, reason=reason),
        )

    return _set


def test_an_approved_charge_maps_to_success(outcome):
    outcome(gateway_client.SUCCESS, "txn-1", "approved")

    result = service.charge(PaymentRequest(order_id=1, amount=100.0))

    assert result.status == "SUCCESS"
    assert result.transaction_id == "txn-1"
    assert result.order_id == 1
    assert result.amount == 100.0


def test_a_declined_charge_maps_to_failed(outcome):
    outcome(gateway_client.FAILED, "txn-2", "declined")

    result = service.charge(PaymentRequest(order_id=2, amount=50.0))

    assert result.status == "FAILED"
    assert result.reason == "declined"


def test_no_answer_maps_to_unknown_and_not_to_failed(outcome):
    outcome(gateway_client.UNKNOWN, None, "retries_exhausted")

    result = service.charge(PaymentRequest(order_id=3, amount=10.0))

    # Collapsing this into FAILED is the bug this test exists to prevent: it
    # would make app/order/service.py release stock for an order that may have
    # been charged.
    assert result.status == "UNKNOWN"
    assert result.reason == "retries_exhausted"


def test_an_unknown_outcome_still_carries_a_transaction_id_placeholder(outcome):
    outcome(gateway_client.UNKNOWN, None, "timeout")

    result = service.charge(PaymentRequest(order_id=4, amount=10.0))

    # The schema requires the field, and there is genuinely no real id to report
    # — that's precisely the information the timeout cost us. `status` is the
    # field callers must branch on, never the presence of an id.
    assert result.transaction_id
    assert result.status == "UNKNOWN"


def test_the_order_amount_is_passed_through_to_the_gateway(monkeypatch):
    calls = []

    def _charge(order_id, amount):
        calls.append((order_id, amount))
        return gateway_client.GatewayOutcome(gateway_client.SUCCESS, "txn", reason="approved")

    monkeypatch.setattr("app.payment.service.gateway_client.charge", _charge)

    service.charge(PaymentRequest(order_id=9, amount=42.5))

    assert calls == [(9, 42.5)]
