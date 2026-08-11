from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.payment import service
from app.payment.schemas import PaymentRequest, PaymentResult

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=PaymentResult, status_code=201)
def create_payment(payload: PaymentRequest, db: Session = Depends(get_db)):
    """Charge endpoint owned by the Payment service (V7).

    Order calls this over HTTP; public clients may also hit it via the
    gateway's `/payments` path.
    """
    return service.charge(db, payload)
