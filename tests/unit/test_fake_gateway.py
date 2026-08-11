"""V5 (Reliability): fake_gateway/, the stand-in payment provider.

Worth testing despite being a stand-in, because two of its behaviours are what
the app's reliability guarantees actually rest on: it must replay a stored
result for a repeated Idempotency-Key (otherwise retrying a charge really would
double-charge), and its chaos knobs must produce the failures the experiments
claim to inject.
"""

import pytest
from fastapi.testclient import TestClient

from fake_gateway.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        test_client.post("/admin/reset")
        yield test_client
        test_client.post("/admin/reset")


def _charge(client, order_id=1, amount=10.0, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/charge", json={"order_id": order_id, "amount": amount}, headers=headers)


# --- idempotency ------------------------------------------------------------


def test_a_repeated_idempotency_key_replays_the_original_result(client):
    client.post("/admin/chaos", json={"success_rate": 1.0})

    first = _charge(client, key="order-1").json()
    second = _charge(client, key="order-1").json()

    # Identical transaction id: the same charge, not a new one. This is the
    # property that makes app/payment/gateway_client.py's retry policy safe
    # rather than a double-charging machine.
    assert second["transaction_id"] == first["transaction_id"]
    assert second["replayed"] is True
    assert first["replayed"] is False


def test_replaying_a_key_does_not_execute_a_second_charge(client):
    client.post("/admin/chaos", json={"success_rate": 1.0})

    for _ in range(5):
        _charge(client, key="order-1")

    counts = client.get("/admin/charges").json()
    assert counts["distinct_idempotency_keys"] == 1
    assert counts["charges_executed"] == 1


def test_different_keys_are_charged_separately(client):
    client.post("/admin/chaos", json={"success_rate": 1.0})

    first = _charge(client, order_id=1, key="order-1").json()
    second = _charge(client, order_id=2, key="order-2").json()

    assert first["transaction_id"] != second["transaction_id"]
    assert client.get("/admin/charges").json()["charges_executed"] == 2


def test_a_stored_key_is_replayed_even_while_the_gateway_is_failing(client):
    client.post("/admin/chaos", json={"success_rate": 1.0})
    first = _charge(client, key="order-1").json()

    # Total outage after the original charge succeeded.
    client.post("/admin/chaos", json={"error_rate": 1.0})
    replayed = _charge(client, key="order-1")

    # Replay is checked before chaos on purpose: if an outage could hide a known
    # result, the client could never learn the outcome of a charge it had already
    # been billed for.
    assert replayed.status_code == 200
    assert replayed.json()["transaction_id"] == first["transaction_id"]


def test_a_missing_idempotency_key_is_never_deduplicated(client):
    client.post("/admin/chaos", json={"success_rate": 1.0})

    first = _charge(client).json()
    second = _charge(client).json()

    # No key means no way to recognise a repeat, so each call is a fresh charge.
    # Exactly why the client always sends one.
    assert first["transaction_id"] != second["transaction_id"]


# --- chaos knobs ------------------------------------------------------------


def test_success_rate_zero_declines_every_charge(client):
    client.post("/admin/chaos", json={"success_rate": 0.0})

    for order_id in range(5):
        assert _charge(client, order_id=order_id, key=f"order-{order_id}").json()["status"] == "FAILED"


def test_error_rate_one_returns_503_on_every_request(client):
    client.post("/admin/chaos", json={"error_rate": 1.0})

    response = _charge(client, key="order-1")

    # A 5xx, not a decline: the gateway is saying it couldn't process the request
    # at all, which is what makes it safe for the client to retry.
    assert response.status_code == 503


def test_a_failed_request_records_no_charge(client):
    client.post("/admin/chaos", json={"error_rate": 1.0})
    _charge(client, key="order-1")

    assert client.get("/admin/charges").json()["charges_executed"] == 0


def test_latency_is_applied_before_answering(client):
    import time

    client.post("/admin/chaos", json={"latency_ms": 250, "success_rate": 1.0})

    started = time.perf_counter()
    _charge(client, key="order-1")
    elapsed = time.perf_counter() - started

    assert elapsed >= 0.25


def test_chaos_config_round_trips(client):
    client.post(
        "/admin/chaos",
        json={"error_rate": 0.5, "hang_rate": 0.25, "hang_ms": 5000, "latency_ms": 10, "success_rate": 0.9},
    )

    config = client.get("/admin/chaos").json()

    assert config["error_rate"] == 0.5
    assert config["hang_rate"] == 0.25
    assert config["success_rate"] == 0.9


def test_chaos_rates_outside_zero_to_one_are_rejected(client):
    assert client.post("/admin/chaos", json={"error_rate": 1.5}).status_code == 422


def test_reset_clears_chaos_and_the_idempotency_store(client):
    client.post("/admin/chaos", json={"error_rate": 1.0, "success_rate": 1.0})
    client.post("/admin/reset")

    assert client.get("/admin/chaos").json()["error_rate"] == 0.0
    assert client.get("/admin/charges").json()["distinct_idempotency_keys"] == 0


def test_health_endpoint_reports_ok(client):
    assert client.get("/health").json()["status"] == "ok"
