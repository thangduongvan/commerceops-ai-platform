import random
import uuid

from app.payment.schemas import PaymentRequest, PaymentResult

# V0 stand-in for an external payment gateway. A real integration would
# call out over the network and need timeout/retry handling (that's V5,
# Reliability). Here we only need a provider that fails often enough to
# exercise the order module's failure-handling path.
SUCCESS_RATE = 0.8


def charge(payload: PaymentRequest) -> PaymentResult:
    succeeded = random.random() < SUCCESS_RATE
    return PaymentResult(
        order_id=payload.order_id,
        status="SUCCESS" if succeeded else "FAILED",
        transaction_id=str(uuid.uuid4()),
        amount=payload.amount,
    )
