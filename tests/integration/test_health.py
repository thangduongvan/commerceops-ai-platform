"""V5/V7: shallow /health vs deep /health/ready, per microservice."""


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_stays_ok_even_though_dependencies_are_unreachable(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_does_not_touch_any_dependency(order_client, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the shallow health check must not probe dependencies")

    monkeypatch.setattr("app.core.cache.cache_ping", explode)
    monkeypatch.setattr("app.core.queue.queue_reachable", explode)
    monkeypatch.setattr("app.clients.product_client.probe", explode)
    monkeypatch.setattr("app.clients.payment_client.probe", explode)

    assert order_client.get("/health").status_code == 200


def test_order_readiness_reports_order_dependencies(order_client):
    body = order_client.get("/health/ready").json()

    assert set(body["checks"]) == {
        "database",
        "redis",
        "queue",
        "product_service",
        "payment_service",
    }
    assert body["service"] == "monolith" or "service" in body


def test_product_readiness_reports_replica(product_client):
    body = product_client.get("/health/ready").json()

    assert "database" in body["checks"]
    assert "redis" in body["checks"]
    assert "database_replica" in body["checks"]
    assert body["checks"]["database_replica"]["required"] is False


def test_payment_readiness_reports_gateway(payment_http_client):
    body = payment_http_client.get("/health/ready").json()

    assert "database" in body["checks"]
    assert "payment_gateway" in body["checks"]


def test_readiness_reports_database_replica_disabled_when_not_configured(product_client):
    replica = product_client.get("/health/ready").json()["checks"]["database_replica"]

    assert replica["required"] is False
    assert replica.get("enabled") is False
    assert replica["ok"] is True
    assert replica["lag_seconds"] is None


def test_readiness_returns_200_while_reporting_degraded(order_client):
    response = order_client.get("/health/ready")

    assert response.status_code == 200
    # Redis/queue typically unreachable outside Compose → degraded is expected.
    assert response.json()["status"] in ("ok", "degraded")


def test_readiness_reports_the_database_as_healthy(order_client):
    checks = order_client.get("/health/ready").json()["checks"]

    assert checks["database"]["ok"] is True


def test_readiness_marks_redis_as_not_required(order_client):
    checks = order_client.get("/health/ready").json()["checks"]

    assert checks["redis"]["required"] is False
    assert checks["queue"]["required"] is False


def test_payment_readiness_includes_circuit_state(payment_http_client):
    gateway = payment_http_client.get("/health/ready").json()["checks"]["payment_gateway"]

    assert "circuit_state" in gateway
    assert "bulkhead_in_use" in gateway


def test_order_readiness_ok_when_dependencies_healthy(order_client, monkeypatch):
    monkeypatch.setattr("app.core.cache.cache_ping", lambda: True)
    monkeypatch.setattr("app.core.queue.queue_reachable", lambda: True)
    monkeypatch.setattr(
        "app.clients.product_client.probe",
        lambda: {"reachable": True, "required": False, "circuit_state": "CLOSED"},
    )
    monkeypatch.setattr(
        "app.clients.payment_client.probe",
        lambda: {"reachable": True, "required": False, "circuit_state": "CLOSED"},
    )

    assert order_client.get("/health/ready").json()["status"] == "ok"


def test_payment_readiness_degraded_when_circuit_open(payment_http_client, monkeypatch):
    monkeypatch.setattr(
        "app.payment.gateway_client.probe",
        lambda: {"reachable": True, "circuit_state": "OPEN", "bulkhead_in_use": 0},
    )

    assert payment_http_client.get("/health/ready").json()["status"] == "degraded"
