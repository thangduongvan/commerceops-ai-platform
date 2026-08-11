from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Payment(Base):
    """Durable payment record owned by the Payment service (V7).

    Idempotency key is order_id: a retry of the same charge returns the
    stored result instead of hitting the gateway again.
    """

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("order_id", name="uq_payments_order_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2, asdecimal=False), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
