from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.queue import publish_event
from app.customer.models import Customer
from app.order.models import Order, OrderItem, OrderStatus
from app.order.schemas import OrderCreate
from app.payment import service as payment_service
from app.payment.schemas import PaymentRequest
from app.product.models import Product


def _line_total(unit_price: float, quantity: int) -> float:
    return round(float(unit_price) * quantity, 2)


def _get_customer_or_404(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found"
        )
    return customer


def _restock(db: Session, order: Order) -> None:
    for item in order.items:
        product = db.get(Product, item.product_id)
        if product is not None:
            product.stock_quantity += item.quantity


def create_order(db: Session, payload: OrderCreate) -> Order:
    _get_customer_or_404(db, payload.customer_id)

    products: dict[int, Product] = {}
    for item in payload.items:
        product = db.get(Product, item.product_id)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {item.product_id} not found",
            )
        if product.stock_quantity < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Insufficient stock for product {item.product_id}",
            )
        products[item.product_id] = product

    # Order + items + stock decrement must succeed or fail together, so
    # we never end up with a persisted order that doesn't match reserved
    # stock (ACID transaction boundary).
    order = Order(customer_id=payload.customer_id, status=OrderStatus.PENDING.value)
    total = 0.0
    for item in payload.items:
        product = products[item.product_id]
        product.stock_quantity -= item.quantity
        total += _line_total(product.price, item.quantity)
        order.items.append(
            OrderItem(
                product_id=product.id,
                quantity=item.quantity,
                unit_price=product.price,
            )
        )
    order.total_amount = total

    db.add(order)
    db.commit()
    db.refresh(order)

    # V4 (Asynchronous Processing): publish, don't call notification/analytics/
    # email/search in-process. app/worker.py fans this one event out to all
    # four side effects off the request path — see docs/adr/ADR-005-async-processing.md.
    publish_event(
        "OrderCreated",
        {"order_id": order.id, "customer_id": order.customer_id, "total_amount": order.total_amount},
    )

    payment_result = payment_service.charge(
        PaymentRequest(order_id=order.id, amount=order.total_amount)
    )

    if payment_result.status == "SUCCESS":
        order.status = OrderStatus.PAID.value
        publish_event(
            "OrderPaid",
            {"order_id": order.id, "transaction_id": payment_result.transaction_id},
        )
    else:
        # Compensating action: release the stock reserved above, since the
        # order did not actually complete. This is a small preview of the
        # Saga/compensation pattern introduced properly at V12.
        _restock(db, order)
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
    if order.status in (OrderStatus.CANCELLED.value, OrderStatus.PAYMENT_FAILED.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order cannot be cancelled from status {order.status}",
        )

    _restock(db, order)
    order.status = OrderStatus.CANCELLED.value
    db.commit()
    db.refresh(order)

    publish_event("OrderCancelled", {"order_id": order.id})
    return order
