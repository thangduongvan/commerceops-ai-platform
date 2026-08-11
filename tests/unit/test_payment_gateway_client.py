"""V5 (Reliability): app/payment/gateway_client.py.

Uses httpx.MockTransport, so no gateway (and no Docker) needs to be running --
the same "tests are fast and portable" philosophy as moto for SQS and SQLite
for the integration tests. MockTransport is what makes it possible to assert on
things a live server can't easily be made to do on demand: the exact number of
attempts, the headers each one carried, and a timeout raised at will.

Retry delays are neutralized per-test by patching settings.retry_base_delay_
seconds to 0, so the 1s/2s/4s ladder doesn't make the suite take 7 seconds per
failing case. The ladder's actual timing is verified in test_reliability.py.
"""

import httpx
import pytest

from app.payment import gateway_client
from app.payment.gateway_client import FAILED, SUCCESS, UNKNOWN


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch):
    monkeypatch.setattr(gateway_client.settings, "retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(gateway_client.settings, "retry_max_delay_seconds", 0.0)


@pytest.fixture(autouse=True)
def fresh_breaker_and_bulkhead():
    """Reset the module-level primitives between tests.

    They're module-level on purpose in production (breaker state must be shared
    across all requests, or it never accumulates enough failures to trip), which
    means tests have to explicitly reset them.
    """
    gateway_client.payment_breaker.reset()
    yield
    gateway_client.payment_breaker.reset()


def _install(monkeypatch, handler):
    """Point the shared client at a MockTransport running `handler`."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://payment-gateway:9000",
    )
    monkeypatch.setattr(gateway_client, "_client", client)
    return client


def _approved(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"order_id": 1, "status": "SUCCESS", "transaction_id": "txn-1", "amount": 10.0},
    )


def test_approved_charge_returns_success(monkeypatch):
    _install(monkeypatch, _approved)

    outcome = gateway_client.charge(order_id=1, amount=10.0)

    assert outcome.status == SUCCESS
    assert outcome.transaction_id == "txn-1"
    assert outcome.reason == "approved"


def test_declined_charge_returns_failed_and_is_not_retried(monkeypatch):
    calls = []

    def declined(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"order_id": 1, "status": "FAILED", "transaction_id": "txn-2", "amount": 10.0},
        )

    _install(monkeypatch, declined)
    outcome = gateway_client.charge(order_id=1, amount=10.0)

    assert outcome.status == FAILED
    # A decline is a successful API call with a negative business answer. The
    # answer won't change on retry, so retrying would turn one decline into
    # four pointless calls.
    assert len(calls) == 1


def test_timeout_is_retried_then_reported_as_unknown(monkeypatch):
    calls = []

    def always_times_out(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("gateway did not answer", request=request)

    _install(monkeypatch, always_times_out)
    monkeypatch.setattr(gateway_client.settings, "payment_retry_attempts", 4)

    outcome = gateway_client.charge(order_id=7, amount=25.0)

    assert len(calls) == 4
    # Crucially UNKNOWN, not FAILED: the gateway accepted the request and then
    # went quiet, so the card may well have been charged. Reporting FAILED here
    # is what would let app/order/service.py release stock for a paid order.
    assert outcome.status == UNKNOWN
    assert outcome.reason == "retries_exhausted"
    assert outcome.transaction_id is None


def test_server_error_is_retried_and_can_succeed(monkeypatch):
    attempts = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, json={"detail": "unavailable"})
        return _approved(request)

    _install(monkeypatch, flaky)
    outcome = gateway_client.charge(order_id=2, amount=10.0)

    # This is the "50% API failure" experiment in miniature: a per-request
    # failure rate is not an order failure rate, because retries absorb it.
    assert outcome.status == SUCCESS
    assert attempts["n"] == 3


def test_client_error_is_not_retried(monkeypatch):
    calls = []

    def bad_request(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"detail": "malformed"})

    _install(monkeypatch, bad_request)
    outcome = gateway_client.charge(order_id=3, amount=10.0)

    # A 4xx means our request was wrong. Retrying an identical bad request
    # cannot help, and it shouldn't be held against the dependency either.
    assert len(calls) == 1
    assert outcome.status == FAILED
    assert gateway_client.payment_breaker.state == "CLOSED"


def test_connection_error_is_retried_and_reported_as_unknown(monkeypatch):
    calls = []

    def refused(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("connection refused", request=request)

    _install(monkeypatch, refused)
    monkeypatch.setattr(gateway_client.settings, "payment_retry_attempts", 3)

    outcome = gateway_client.charge(order_id=4, amount=10.0)

    assert len(calls) == 3
    assert outcome.status == UNKNOWN


def test_idempotency_key_is_stable_across_every_retry(monkeypatch):
    keys = []

    def capture_then_fail(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers.get("Idempotency-Key"))
        raise httpx.ReadTimeout("no answer", request=request)

    _install(monkeypatch, capture_then_fail)
    monkeypatch.setattr(gateway_client.settings, "payment_retry_attempts", 4)

    gateway_client.charge(order_id=99, amount=10.0)

    # The single most important assertion in this file. A fresh key per attempt
    # would make this retry policy a double-charging machine: attempt 1 may have
    # succeeded before timing out, and only an identical key lets the gateway
    # recognise attempt 2 as the same charge and replay the original result.
    assert keys == ["order-99"] * 4
    assert len(set(keys)) == 1


def test_idempotency_key_is_stable_across_separate_charge_calls(monkeypatch):
    keys = []

    def capture(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers.get("Idempotency-Key"))
        return _approved(request)

    _install(monkeypatch, capture)

    gateway_client.charge(order_id=5, amount=10.0)
    gateway_client.charge(order_id=5, amount=10.0)

    # Derived from the order, not generated per call, so even a retry from a
    # different process or after a restart deduplicates correctly.
    assert keys == ["order-5", "order-5"]


def test_circuit_opens_after_repeated_failures_and_then_fails_fast(monkeypatch):
    calls = []

    def always_fails(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("refused", request=request)

    _install(monkeypatch, always_fails)
    monkeypatch.setattr(gateway_client.settings, "payment_retry_attempts", 2)

    threshold = gateway_client.settings.circuit_breaker_failure_threshold
    for order_id in range(threshold):
        assert gateway_client.charge(order_id=order_id, amount=10.0).status == UNKNOWN

    assert gateway_client.payment_breaker.state == "OPEN"

    calls_before = len(calls)
    outcome = gateway_client.charge(order_id=1000, amount=10.0)

    assert outcome.status == UNKNOWN
    assert outcome.reason == "circuit_open"
    # No HTTP attempt at all: the whole point is that this order didn't spend
    # the retry budget rediscovering a failure already known about.
    assert len(calls) == calls_before


def test_bulkhead_rejection_is_reported_as_unknown(monkeypatch):
    _install(monkeypatch, _approved)

    class FullBulkhead:
        name = "payment_gateway"
        in_use = 10

        def call(self, fn):
            raise gateway_client.BulkheadFullError("full")

    monkeypatch.setattr(gateway_client, "payment_bulkhead", FullBulkhead())
    outcome = gateway_client.charge(order_id=6, amount=10.0)

    # Shed load, but honestly: we never asked the gateway, so we can't claim
    # the payment failed. Same UNKNOWN handling as a timeout.
    assert outcome.status == UNKNOWN
    assert outcome.reason == "bulkhead_full"


def test_probe_reports_reachability_and_circuit_state(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"}))

    state = gateway_client.probe()

    assert state["reachable"] is True
    assert state["circuit_state"] == "CLOSED"


def test_probe_reports_unreachable_without_raising(monkeypatch):
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install(monkeypatch, refused)

    state = gateway_client.probe()

    assert state["reachable"] is False
    # A health probe that raises is a health probe that takes down the endpoint
    # reporting health.
    assert "error" in state


def test_probe_does_not_trip_the_breaker(monkeypatch):
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install(monkeypatch, refused)

    for _ in range(gateway_client.settings.circuit_breaker_failure_threshold + 2):
        gateway_client.probe()

    # Health checks observe; they must not consume the failure budget or the
    # bulkhead's capacity, or monitoring would itself cause the outage it reports.
    assert gateway_client.payment_breaker.state == "CLOSED"
