from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients import payment_client, product_client
from app.clients.product_client import ProductServiceError, ProductServiceUnavailable
from app.core.queue import publish_event
from app.customer.models import Customer
from app.order.models import Order, OrderItem, OrderStatus
from app.order.schemas import OrderCreate
from app.payment.schemas import PaymentRequest


def _line_total(unit_price: float, quantity: int) -> float:
    return round(float(unit_price) * quantity, 2)


def _get_customer_or_404(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found"
        )
    return customer


def _release_items(order: Order) -> None:
    """Compensating stock release via the Product service (V7)."""
    items = [
        {"product_id": item.product_id, "quantity": item.quantity} for item in order.items
    ]
    if items:
        product_client.release(items)


def create_order(db: Session, payload: OrderCreate) -> Order:
    _get_customer_or_404(db, payload.customer_id)

    reserve_payload = [
        {"product_id": item.product_id, "quantity": item.quantity} for item in payload.items
    ]
    try:
        reserved = product_client.reserve(reserve_payload)
    except ProductServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ProductServiceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Product service unavailable: {exc}",
        ) from exc

    # Order + items in the Order DB only. Stock already reserved in Product DB —
    # the single-ACID boundary of the monolith is gone (see ADR-008).
    order = Order(customer_id=payload.customer_id, status=OrderStatus.PENDING.value)
    total = 0.0
    for line in reserved:
        total += _line_total(line["unit_price"], line["quantity"])
        order.items.append(
            OrderItem(
                product_id=line["product_id"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
            )
        )
    order.total_amount = total

    db.add(order)
    db.commit()
    db.refresh(order)

    publish_event(
        "OrderCreated",
        {
            "order_id": order.id,
            "customer_id": order.customer_id,
            "total_amount": order.total_amount,
        },
    )

    payment_result = payment_client.charge(
        PaymentRequest(order_id=order.id, amount=order.total_amount)
    )

    if payment_result.status == "SUCCESS":
        order.status = OrderStatus.PAID.value
        publish_event(
            "OrderPaid",
            {"order_id": order.id, "transaction_id": payment_result.transaction_id},
        )
    elif payment_result.status == "UNKNOWN":
        # Deliberately NOT releasing stock — same V5 rationale.
        order.status = OrderStatus.PAYMENT_PENDING.value
        publish_event(
            "OrderPaymentUnconfirmed",
            {"order_id": order.id, "reason": payment_result.reason},
        )
    else:
        try:
            _release_items(order)
        except ProductServiceUnavailable:
            # Stock may remain reserved; reconciliation is V12's job.
            pass
        order.status = OrderStatus.PAYMENT_FAILED.value
        publish_event("OrderPaymentFailed", {"order_id": order.id})

    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


def list_orders(
    db: Session, customer_id: int | None = None, skip: int = 0, limit: int = 50
) -> list[Order]:
    stmt = select(Order).order_by(Order.id).offset(skip).limit(limit)
    if customer_id is not None:
        stmt = stmt.where(Order.customer_id == customer_id)
    return list(db.execute(stmt).scalars())


def cancel_order(db: Session, order_id: int) -> Order:
    order = get_order(db, order_id)
    if order.status in (
        OrderStatus.CANCELLED.value,
        OrderStatus.PAYMENT_FAILED.value,
        OrderStatus.PAYMENT_PENDING.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order cannot be cancelled from status {order.status}",
        )

    try:
        _release_items(order)
    except ProductServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ProductServiceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Product service unavailable: {exc}",
        ) from exc

    order.status = OrderStatus.CANCELLED.value
    db.commit()
    db.refresh(order)

    publish_event("OrderCancelled", {"order_id": order.id})
    return order
