import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"

    # V5 (Reliability): the gateway never answered, so we genuinely do not
    # know whether the card was charged. Distinct from PAYMENT_FAILED, which
    # means the gateway answered and declined.
    #
    # Collapsing the two would force a guess, and both guesses are bad: call
    # it PAID and we might ship goods nobody paid for; call it PAYMENT_FAILED
    # and we release stock for an order the customer was charged for. Keeping
    # the uncertainty in the data model is the honest option — an order in
    # this state is waiting on reconciliation against the gateway, which is
    # V12's (Saga) job. See docs/adr/ADR-006-reliability.md.
    PAYMENT_PENDING = "PAYMENT_PENDING"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    status: Mapped[str] = mapped_column(default=OrderStatus.PENDING.value, nullable=False)
    total_amount: Mapped[float] = mapped_column(
        Numeric(10, 2, asdecimal=False), nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    # V7: logical reference only — products live in the Product service DB.
    # No cross-database foreign key (database-per-service).
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2, asdecimal=False), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")
