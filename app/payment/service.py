import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.payment import gateway_client
from app.payment.models import Payment
from app.payment.schemas import PaymentRequest, PaymentResult

logger = logging.getLogger("commerceops.payment")


def charge(db: Session, payload: PaymentRequest) -> PaymentResult:
    """Charge an order through the third-party gateway and persist the result.

    V7: Payment owns a `payments` table. order_id is the idempotency key —
    a retry returns the stored row instead of charging again.

    Three outcomes (unchanged from V5):

      SUCCESS  -- charged.
      FAILED   -- the gateway answered and declined. Safe to release stock.
      UNKNOWN  -- we never got an answer. The charge may or may not have
                  happened, so the order is *not* treated as failed.
    """
    existing = db.execute(
        select(Payment).where(Payment.order_id == payload.order_id)
    ).scalar_one_or_none()
    if existing is not None:
        return PaymentResult(
            order_id=existing.order_id,
            status=existing.status,
            transaction_id=existing.transaction_id,
            amount=existing.amount,
            reason=existing.reason,
        )

    outcome = gateway_client.charge(payload.order_id, payload.amount)
    if outcome.status != gateway_client.SUCCESS:
        logger.warning(
            "payment_outcome order_id=%s status=%s reason=%s",
            payload.order_id,
            outcome.status,
            outcome.reason,
        )

    transaction_id = outcome.transaction_id or str(uuid.uuid4())
    row = Payment(
        order_id=payload.order_id,
        amount=payload.amount,
        status=outcome.status,
        transaction_id=transaction_id,
        reason=outcome.reason or "",
    )
    db.add(row)
    db.commit()

    return PaymentResult(
        order_id=payload.order_id,
        status=outcome.status,
        transaction_id=transaction_id,
        amount=payload.amount,
        reason=outcome.reason or "",
    )
