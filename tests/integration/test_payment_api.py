from unittest.mock import patch


def test_payment_endpoint_returns_success(client):
    with patch("app.payment.service.random.random", return_value=0.1):
        response = client.post("/payments", json={"order_id": 1, "amount": 25.0})

    assert response.status_code == 201
    assert response.json()["status"] == "SUCCESS"


def test_payment_endpoint_returns_failed(client):
    with patch("app.payment.service.random.random", return_value=0.95):
        response = client.post("/payments", json={"order_id": 1, "amount": 25.0})

    assert response.status_code == 201
    assert response.json()["status"] == "FAILED"
