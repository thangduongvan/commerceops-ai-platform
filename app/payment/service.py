import logging
import uuid

from app.payment import gateway_client
from app.payment.schemas import PaymentRequest, PaymentResult

logger = logging.getLogger("commerceops.payment")


def charge(payload: PaymentRequest) -> PaymentResult:
    """Charge an order through the third-party gateway.

    Through V4 this was an in-process `random.random()` coin flip. V5 makes it
    a real HTTP call to fake_gateway/ (a Compose service locally, an ECS
    sidecar in AWS), because none of V5's requirements are expressible against
    a function call: you cannot time out something that never touches the
    network, and you cannot inject "payment timeout" or "50% API failure"
    into it either. All of the timeout/retry/breaker/bulkhead handling lives
    in app/payment/gateway_client.py.

    Three outcomes now, not two:

      SUCCESS  -- charged.
      FAILED   -- the gateway answered and declined. Safe to release stock.
      UNKNOWN  -- we never got an answer (timeout, retries exhausted, open
                  circuit, shed by the bulkhead). The charge may or may not
                  have happened, so the order is *not* treated as failed --
                  see app/order/service.py and docs/api.md.
    """
    outcome = gateway_client.charge(payload.order_id, payload.amount)
    if outcome.status != gateway_client.SUCCESS:
        logger.warning(
            "payment_outcome order_id=%s status=%s reason=%s",
            payload.order_id,
            outcome.status,
            outcome.reason,
        )
    return PaymentResult(
        order_id=payload.order_id,
        status=outcome.status,
        # An UNKNOWN outcome has no transaction id by definition -- that's
        # exactly the information we didn't receive.
        transaction_id=outcome.transaction_id or str(uuid.uuid4()),
        amount=payload.amount,
        reason=outcome.reason,
    )
