from unittest.mock import patch


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
