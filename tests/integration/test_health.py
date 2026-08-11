"""V5 (Reliability): the two health endpoints and why they differ.

The load-bearing test here is that /health keeps returning 200 with every
dependency down. It reads like a weaker assertion than checking dependencies,
and it is deliberately the opposite: the ALB uses /health to decide whether to
keep routing to a task, so a check that fails when a *shared* dependency fails
marks every task unhealthy at once, empties the target group, and turns a
degraded system into a completely unavailable one.

Redis and the payment gateway are genuinely unreachable in the test environment
(the Compose hostnames don't resolve outside a container), so these tests
exercise the real failure paths rather than mocked ones.
"""


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_stays_ok_even_though_redis_and_the_gateway_are_unreachable(client):
    # Nothing is mocked: Redis and the payment gateway really are unreachable
    # here. The shallow check must not care.
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_does_not_touch_any_dependency(client, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the shallow health check must not probe dependencies")

    monkeypatch.setattr("app.main.cache_ping", explode)
    monkeypatch.setattr("app.main.queue_reachable", explode)
    monkeypatch.setattr("app.payment.gateway_client.probe", explode)

    assert client.get("/health").status_code == 200


def test_readiness_reports_every_dependency(client):
    body = client.get("/health/ready").json()

    assert set(body["checks"]) == {
        "database",
        "redis",
        "queue",
        "payment_gateway",
    }
    assert "status" in body


def test_readiness_returns_200_while_reporting_degraded(client):
    response = client.get("/health/ready")

    # Deliberately not a 5xx. This endpoint exists to tell an operator what is
    # broken; a failing status code would invite something automated to start
    # restarting tasks, reintroducing the cascade the shallow check avoids.
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_readiness_reports_the_database_as_healthy(client):
    checks = client.get("/health/ready").json()["checks"]

    # SQLite stands in for Postgres in tests, but the probe path is the same:
    # SELECT 1 through the engine.
    assert checks["database"]["ok"] is True


def test_readiness_marks_redis_as_not_required(client):
    checks = client.get("/health/ready").json()["checks"]

    # Redis being down degrades latency, not correctness — every cache helper
    # falls through to Postgres (V3). Recording that explicitly stops a future
    # reader from concluding the app needs Redis to serve traffic.
    assert checks["redis"]["required"] is False
    assert checks["queue"]["required"] is False


def test_readiness_includes_the_payment_circuit_state(client):
    gateway = client.get("/health/ready").json()["checks"]["payment_gateway"]

    # "Reachable but the breaker is open" is a real and confusing state (the
    # breaker is waiting out its recovery window), and a plain ping can't explain
    # it. Reporting both is what makes the answer actionable.
    assert "circuit_state" in gateway
    assert "bulkhead_in_use" in gateway
    assert gateway["reachable"] is False


def test_readiness_reports_ok_when_everything_is_healthy(client, monkeypatch):
    monkeypatch.setattr("app.main.cache_ping", lambda: True)
    monkeypatch.setattr("app.main.queue_reachable", lambda: True)
    monkeypatch.setattr(
        "app.payment.gateway_client.probe",
        lambda: {"reachable": True, "circuit_state": "CLOSED", "bulkhead_in_use": 0},
    )

    assert client.get("/health/ready").json()["status"] == "ok"


def test_readiness_is_degraded_when_the_circuit_is_open(client, monkeypatch):
    monkeypatch.setattr("app.main.cache_ping", lambda: True)
    monkeypatch.setattr("app.main.queue_reachable", lambda: True)
    monkeypatch.setattr(
        "app.payment.gateway_client.probe",
        lambda: {"reachable": True, "circuit_state": "OPEN", "bulkhead_in_use": 0},
    )

    # Reachable, but the app has stopped calling it — which is a degraded system
    # even though every ping succeeds.
    assert client.get("/health/ready").json()["status"] == "degraded"
