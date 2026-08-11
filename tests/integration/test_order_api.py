from unittest.mock import patch

import pytest


# V4: app/order/service.py now publishes events via app.core.queue's
# publish_event instead of calling notification in-process. Autouse-patching
# it here (same style as the payment-randomness patch below) keeps every
# other test in this file focused on order/payment/stock logic without
# needing a real/mocked SQS; test_create_order_publishes_order_events below
# is the one test that actually asserts on it.
@pytest.fixture(autouse=True)
def published_events(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.order.service.publish_event",
        lambda event_type, payload: calls.append((event_type, payload)),
    )
    return calls


def _create_customer(client, email="carol@example.com"):
    response = client.post("/customers", json={"name": "Carol", "email": email})
    return response.json()["id"]


def _create_product(client, stock=10):
    response = client.post("/products", json={"name": "Gadget", "price": 20.0, "stock_quantity": stock})
    return response.json()["id"]


def test_create_order_success_path_decrements_stock_and_pays(client):
    customer_id = _create_customer(client)
    product_id = _create_product(client, stock=10)

    with patch("app.payment.service.random.random", return_value=0.1):
        response = client.post(
            "/orders",
            json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 3}]},
        )

    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "PAID"
    assert order["total_amount"] == 60.0

    product = client.get(f"/products/{product_id}").json()
    assert product["stock_quantity"] == 7


def test_create_order_payment_failure_restocks_items(client):
    customer_id = _create_customer(client)
    product_id = _create_product(client, stock=10)

    with patch("app.payment.service.random.random", return_value=0.99):
        response = client.post(
            "/orders",
            json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 4}]},
        )

    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "PAYMENT_FAILED"

    product = client.get(f"/products/{product_id}").json()
    assert product["stock_quantity"] == 10


def test_create_order_rejects_insufficient_stock(client):
    customer_id = _create_customer(client)
    product_id = _create_product(client, stock=1)

    response = client.post(
        "/orders",
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 5}]},
    )
    assert response.status_code == 409


def test_create_order_rejects_unknown_customer(client):
    product_id = _create_product(client, stock=1)

    response = client.post(
        "/orders",
        json={"customer_id": 999999, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    assert response.status_code == 404


def test_cancel_order_restocks_and_blocks_double_cancel(client):
    customer_id = _create_customer(client)
    product_id = _create_product(client, stock=10)

    with patch("app.payment.service.random.random", return_value=0.1):
        order = client.post(
            "/orders",
            json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 2}]},
        ).json()

    cancel_resp = client.post(f"/orders/{order['id']}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    product = client.get(f"/products/{product_id}").json()
    assert product["stock_quantity"] == 10

    second_cancel = client.post(f"/orders/{order['id']}/cancel")
    assert second_cancel.status_code == 409


def test_create_order_publishes_order_events(client, published_events):
    customer_id = _create_customer(client)
    product_id = _create_product(client, stock=10)
    published_events.clear()  # drop the customer/product creation noise, if any

    with patch("app.payment.service.random.random", return_value=0.1):
        response = client.post(
            "/orders",
            json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
        )
    order_id = response.json()["id"]

    event_types = [event_type for event_type, _ in published_events]
    assert event_types == ["OrderCreated", "OrderPaid"]
    assert published_events[0][1]["order_id"] == order_id
    assert published_events[1][1]["order_id"] == order_id


def test_create_order_payment_failure_publishes_payment_failed_event(client, published_events):
    customer_id = _create_customer(client)
    product_id = _create_product(client, stock=10)
    published_events.clear()

    with patch("app.payment.service.random.random", return_value=0.99):
        client.post(
            "/orders",
            json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
        )

    event_types = [event_type for event_type, _ in published_events]
    assert event_types == ["OrderCreated", "OrderPaymentFailed"]


def test_cancel_order_publishes_cancelled_event(client, published_events):
    customer_id = _create_customer(client)
    product_id = _create_product(client, stock=10)

    with patch("app.payment.service.random.random", return_value=0.1):
        order = client.post(
            "/orders",
            json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
        ).json()
    published_events.clear()

    client.post(f"/orders/{order['id']}/cancel")

    assert [event_type for event_type, _ in published_events] == ["OrderCancelled"]


def test_list_orders_filters_by_customer(client):
    customer_a = _create_customer(client, email="carol.a@example.com")
    customer_b = _create_customer(client, email="carol.b@example.com")
    product_id = _create_product(client, stock=10)

    with patch("app.payment.service.random.random", return_value=0.1):
        client.post(
            "/orders",
            json={"customer_id": customer_a, "items": [{"product_id": product_id, "quantity": 1}]},
        )
        client.post(
            "/orders",
            json={"customer_id": customer_b, "items": [{"product_id": product_id, "quantity": 1}]},
        )

    response = client.get(f"/orders?customer_id={customer_a}")
    assert response.status_code == 200
    orders = response.json()
    assert len(orders) == 1
    assert orders[0]["customer_id"] == customer_a
