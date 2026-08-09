from pydantic import BaseModel, Field


class PaymentRequest(BaseModel):
    order_id: int
    amount: float = Field(gt=0)


class PaymentResult(BaseModel):
    order_id: int
    status: str  # "SUCCESS" | "FAILED"
    transaction_id: str
    amount: float
