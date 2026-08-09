from unittest.mock import patch

from app.payment import service
from app.payment.schemas import PaymentRequest


def test_charge_returns_success_when_random_below_success_rate():
    request = PaymentRequest(order_id=1, amount=100.0)

    with patch("app.payment.service.random.random", return_value=0.1):
        result = service.charge(request)

    assert result.status == "SUCCESS"
    assert result.order_id == 1
    assert result.amount == 100.0


def test_charge_returns_failed_when_random_above_success_rate():
    request = PaymentRequest(order_id=2, amount=50.0)

    with patch("app.payment.service.random.random", return_value=0.95):
        result = service.charge(request)

    assert result.status == "FAILED"


def test_charge_generates_unique_transaction_ids():
    request = PaymentRequest(order_id=3, amount=10.0)

    first = service.charge(request)
    second = service.charge(request)

    assert first.transaction_id != second.transaction_id
