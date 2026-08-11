def test_payment_endpoint_returns_success(client, payment):
    payment.approves()

    response = client.post("/payments", json={"order_id": 1, "amount": 25.0})

    assert response.status_code == 201
    assert response.json()["status"] == "SUCCESS"


def test_payment_endpoint_returns_failed(client, payment):
    payment.declines()

    response = client.post("/payments", json={"order_id": 1, "amount": 25.0})

    assert response.status_code == 201
    assert response.json()["status"] == "FAILED"


def test_payment_endpoint_reports_unknown_when_the_gateway_never_answers(client, payment):
    payment.never_answers(reason="timeout")

    response = client.post("/payments", json={"order_id": 1, "amount": 25.0})

    # V5: the third outcome. The endpoint still returns 201 — the request was
    # handled correctly, it's the *charge* whose result is undetermined, and the
    # `reason` field is what tells a caller which of the two failure shapes
    # ("declined" vs "we never heard back") it is looking at.
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "UNKNOWN"
    assert body["reason"] == "timeout"
