from pydantic import BaseModel, Field


class PaymentRequest(BaseModel):
    order_id: int
    amount: float = Field(gt=0)


class PaymentResult(BaseModel):
    order_id: int
    # V5: "UNKNOWN" joins SUCCESS/FAILED — the gateway never answered, so
    # whether the card was charged is genuinely undetermined.
    status: str  # "SUCCESS" | "FAILED" | "UNKNOWN"
    transaction_id: str
    amount: float
    # V5: why we got this status ("approved", "declined", "timeout",
    # "circuit_open", "bulkhead_full", "retries_exhausted"). Distinguishing
    # "declined" from "circuit_open" is the difference between a customer
    # problem and an outage, and both used to look identical from here.
    reason: str = ""
