from fastapi import APIRouter

from app.payment import service
from app.payment.schemas import PaymentRequest, PaymentResult

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=PaymentResult, status_code=201)
def create_payment(payload: PaymentRequest):
    """Standalone endpoint for the fake payment provider.

    The order module also calls `service.charge` directly in-process
    (monolith == no network hop needed), but exposing this endpoint
    documents the external contract this provider will have once it is
    a real, separately-deployed system.
    """
    return service.charge(payload)
