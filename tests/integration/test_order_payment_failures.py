"""V5 (Reliability): how the order flow reacts to each payment outcome.

The distinction being pinned down here is the one V5 introduced and the one most
likely to be "simplified" back out by a future change: a declined charge and an
unanswered charge are not the same event, and they must not compensate the same
way. Getting this wrong is how you either oversell inventory or charge a
customer for goods you then release to someone else.

See docs/adr/ADR-006-reliability.md and app/order/models.py's PAYMENT_PENDING.
"""

import pytest


@pytest.fixture(autouse=True)
def published_events(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.order.service.publish_event",
        lambda event_type, payload: calls.append((event_type, payload)),
    )
    return calls


def _customer(client, email="dave@example.com"):
    return client.post("/customers", json={"name": "Dave", "email": email}).json()["id"]


def _product(client, stock=10):
    return client.post(
        "/products", json={"name": "Reliable Widget", "price": 10.0, "stock_quantity": stock}
    ).json()["id"]


def _order(client, customer_id, product_id, quantity=2):
    return client.post(
        "/orders",
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": quantity}]},
    )


def _stock(client, product_id):
    return client.get(f"/products/{product_id}").json()["stock_quantity"]


# --- declined: unambiguous failure, so compensate ---------------------------


def test_a_declined_charge_fails_the_order_and_releases_stock(client, payment):
    customer_id = _customer(client)
    product_id = _product(client, stock=10)
    payment.declines()

    order = _order(client, customer_id, product_id, quantity=3).json()

    assert order["status"] == "PAYMENT_FAILED"
    # The gateway answered: no money moved, so the reservation is definitely
    # safe to release.
    assert _stock(client, product_id) == 10


def test_a_declined_charge_publishes_order_payment_failed(client, payment, published_events):
    customer_id = _customer(client)
    product_id = _product(client)
    payment.declines()
    published_events.clear()

    _order(client, customer_id, product_id)

    assert [event_type for event_type, _ in published_events] == ["OrderCreated", "OrderPaymentFailed"]


# --- unanswered: ambiguous, so preserve the uncertainty ---------------------


def test_an_unanswered_charge_leaves_the_order_payment_pending(client, payment):
    customer_id = _customer(client)
    product_id = _product(client, stock=10)
    payment.never_answers()

    order = _order(client, customer_id, product_id, quantity=3).json()

    # Not PAYMENT_FAILED. We never got an answer, so claiming the payment failed
    # would be asserting something we don't know.
    assert order["status"] == "PAYMENT_PENDING"


def test_an_unanswered_charge_does_not_release_stock(client, payment):
    customer_id = _customer(client)
    product_id = _product(client, stock=10)
    payment.never_answers()

    _order(client, customer_id, product_id, quantity=3)

    # The single most important assertion in this file. If the charge did go
    # through, releasing this stock would let someone else buy goods a paying
    # customer has already been billed for. Holding it costs availability of
    # three units until reconciliation resolves the order — recoverable, and
    # visible in the PAYMENT_PENDING status.
    assert _stock(client, product_id) == 7


def test_an_unanswered_charge_publishes_order_payment_unconfirmed(client, payment, published_events):
    customer_id = _customer(client)
    product_id = _product(client)
    payment.never_answers(reason="circuit_open")
    published_events.clear()

    _order(client, customer_id, product_id)

    event_types = [event_type for event_type, _ in published_events]
    # A distinct event type, so downstream consumers can tell "this needs
    # reconciling" apart from "this definitively failed".
    assert event_types == ["OrderCreated", "OrderPaymentUnconfirmed"]
    assert published_events[1][1]["reason"] == "circuit_open"


@pytest.mark.parametrize("reason", ["timeout", "retries_exhausted", "circuit_open", "bulkhead_full"])
def test_every_no_answer_reason_yields_payment_pending(client, payment, reason):
    customer_id = _customer(client, email=f"dave-{reason}@example.com")
    product_id = _product(client)
    payment.never_answers(reason=reason)

    order = _order(client, customer_id, product_id, quantity=1).json()

    # A timeout, an exhausted retry budget, an open circuit and a shed request
    # are four different causes of the same epistemic state: we don't know.
    # They must all be handled identically.
    assert order["status"] == "PAYMENT_PENDING"


def test_a_payment_pending_order_cannot_be_cancelled(client, payment):
    customer_id = _customer(client)
    product_id = _product(client, stock=10)
    payment.never_answers()

    order = _order(client, customer_id, product_id, quantity=2).json()
    response = client.post(f"/orders/{order['id']}/cancel")

    # Cancelling restocks, which is exactly what the UNKNOWN branch refused to
    # do. Allowing it here would reintroduce the bug through the back door, so
    # the order has to be reconciled into PAID or PAYMENT_FAILED first.
    assert response.status_code == 409
    assert _stock(client, product_id) == 8


def test_a_payment_pending_order_is_readable_and_listed(client, payment):
    customer_id = _customer(client)
    product_id = _product(client)
    payment.never_answers()

    order = _order(client, customer_id, product_id, quantity=1).json()

    # Whoever (or whatever) reconciles these has to be able to find them.
    assert client.get(f"/orders/{order['id']}").json()["status"] == "PAYMENT_PENDING"
    listed = client.get(f"/orders?customer_id={customer_id}").json()
    assert [o["status"] for o in listed] == ["PAYMENT_PENDING"]


# --- the reliability layer never breaks the order itself --------------------


def test_a_failed_event_publish_does_not_fail_the_order(client, payment, monkeypatch):
    customer_id = _customer(client)
    product_id = _product(client)
    payment.approves()

    monkeypatch.setattr("app.order.service.publish_event", lambda event_type, payload: None)

    response = _order(client, customer_id, product_id, quantity=1)

    # V4's contract, still holding in V5: a queue outage costs this order its
    # async side effects, never the order itself.
    assert response.status_code == 201
    assert response.json()["status"] == "PAID"
