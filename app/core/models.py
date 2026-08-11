"""V5 (Reliability): infrastructure-level tables that belong to no domain.

Everything else mapping to a table lives in its own domain module
(app/customer/models.py, app/order/models.py, ...). processed_events isn't
about customers or orders — it's the durable record that a side effect
already ran, used by app/core/idempotency.py — so it lives beside the other
core plumbing instead.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProcessedEvent(Base):
    """One row per (event, handler) pair that has definitely completed.

    The unit of deduplication is a *business effect*, not a message. V4 kept
    one Redis key per event_id, which meant "this event was seen" — but with
    four handlers fanning out from each event, seen-ness is the wrong
    question. If the email handler succeeded and the search handler failed,
    the redelivered message must re-run search and must not re-send the
    email. Only per-handler records can express that.

    This also fixes V4's ordering bug outright: the marker was written
    *before* the handlers ran, so a mid-fan-out failure permanently masked
    the message as a duplicate and its remaining side effects were lost for
    the 24-hour TTL. Rows here are written only after a handler returns.

    Postgres, not Redis, because this is the authority: a record that can be
    evicted under memory pressure or lost on restart cannot be the only thing
    preventing a duplicate charge. Redis stays in front of it as a cache (see
    app/core/idempotency.py). Discussed in docs/adr/ADR-006-reliability.md.
    """

    __tablename__ = "processed_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    handler_name: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        # The constraint is what actually enforces idempotency. The read in
        # completed_handlers() can always be raced by a concurrent worker; a
        # unique violation on insert cannot. Correctness rests on the
        # database, not on checking first.
        UniqueConstraint("event_id", "handler_name", name="uq_processed_events_event_handler"),
        Index("ix_processed_events_event_id", "event_id"),
    )
