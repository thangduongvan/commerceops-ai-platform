"""V5 (Reliability): making at-least-once delivery safe.

SQS guarantees at-least-once, which means duplicates are not an edge case,
they are the contract. The spec's requirement is precise: the same event_id
arriving twice "must not cause duplicate business effects."

V4 answered that with a single Redis `SET NX` per event_id. Two things were
wrong with it, both fixed here:

1. **Redis was the authority.** A key that can be evicted under memory
   pressure, lost on restart, or unreachable during a partition cannot be the
   only thing preventing a duplicate charge. It fails open by design, so
   under exactly the conditions that cause redelivery it also stops
   deduplicating.
2. **The unit was the message, not the effect.** One key per event_id, set
   *before* the handlers ran. If handler 3 of 4 raised, the redelivery was
   skipped as a duplicate and handlers 3 and 4 never ran at all.

The fix is two mechanisms with two different jobs, and being clear about
which one correctness depends on:

* **Lease** (Redis, best-effort, expiring) — stops two workers doing the same
  work *at the same time*. Losing it costs duplicated effort, never
  correctness. Optimization only.
* **Processed record** (Postgres, durable, per (event_id, handler)) — the
  authority on whether a business effect already happened. Written only after
  the handler returns, and protected by a unique constraint so a lost race
  fails on insert rather than silently double-acting.

See docs/adr/ADR-006-reliability.md.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.cache import cache_delete, cache_get_json, cache_set_if_absent, cache_set_json
from app.core.config import settings
from app.core.models import ProcessedEvent

logger = logging.getLogger("commerceops.idempotency")


def _lease_key(event_id: str) -> str:
    return f"event_lease:{event_id}"


def _completed_key(event_id: str) -> str:
    return f"event_handlers_done:{event_id}"


def acquire_lease(event_id: str, ttl_seconds: Optional[int] = None) -> bool:
    """Try to claim exclusive in-flight processing of an event.

    Returns True if this worker holds the claim. False means another worker is
    working on it right now, and the caller should leave the message alone so
    the other worker can finish and delete it.

    The TTL matters: it is sized to the SQS visibility timeout, so the lease
    expires at roughly the moment SQS would let another consumer receive the
    same message anyway. A longer lease would block the legitimate retry after
    a worker crash; a shorter one would let a second worker in while the first
    is still going.

    Fails open (Redis down => claim granted), which is safe precisely because
    it isn't load-bearing: the Postgres records below still prevent duplicate
    effects, and a unique-violation on insert catches the race.
    """
    return cache_set_if_absent(
        _lease_key(event_id),
        ttl_seconds or settings.idempotency_lease_ttl_seconds,
    )


def release_lease(event_id: str) -> None:
    """Give up the claim on an event we've stopped working on.

    Called when the worker finishes a message with handlers still outstanding.
    Without this, the lease would keep looking "in flight" until its TTL
    expired, and the redelivery SQS makes at the visibility timeout would be
    skipped as concurrent work — costing a whole extra visibility timeout
    before the failed handlers get another attempt.

    A crashed worker can't reach this, which is exactly why the lease has a TTL
    at all: expiry is the fallback for the case where nothing gets to clean up.
    """
    cache_delete(_lease_key(event_id))


def completed_handlers(db: Session, event_id: str) -> set[str]:
    """Which handlers have already completed for this event.

    Redis first (a redelivery is usually seconds old, so the cache hits and
    saves a query), Postgres as the fallback *and* the authority. A dead Redis
    makes this slower, never wrong — the same "Redis optimizes, Postgres
    decides" rule the cache module has followed since V3.
    """
    cached = cache_get_json(_completed_key(event_id))
    if isinstance(cached, list):
        return set(cached)

    rows = db.execute(
        select(ProcessedEvent.handler_name).where(ProcessedEvent.event_id == event_id)
    ).scalars()
    handlers = set(rows)
    if handlers:
        _refresh_cache(event_id, handlers)
    return handlers


def _refresh_cache(event_id: str, handlers: set[str]) -> None:
    cache_set_json(
        _completed_key(event_id),
        sorted(handlers),
        settings.sqs_idempotency_ttl_seconds,
    )


def record_handler_done(db: Session, event_id: str, event_type: str, handler_name: str) -> None:
    """Durably record that one handler finished for one event.

    Called only *after* the handler returns successfully — that ordering is
    the entire point. A pre-emptive marker (V4's mistake) records intent, not
    completion, and intent is exactly what a crash invalidates.

    A concurrent worker that got in first trips the unique constraint; that's
    a successful outcome, not an error, so it's rolled back and swallowed. The
    constraint, not the read in completed_handlers(), is what makes this
    correct under concurrency.
    """
    db.add(
        ProcessedEvent(event_id=event_id, handler_name=handler_name, event_type=event_type)
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info(
            "handler already recorded (concurrent worker won the race) event_id=%s handler=%s",
            event_id,
            handler_name,
        )

    # Keep the read cache honest rather than waiting for its TTL, so a
    # redelivery moments later doesn't re-run a handler we just completed.
    cached = cache_get_json(_completed_key(event_id))
    handlers = set(cached) if isinstance(cached, list) else set()
    handlers.add(handler_name)
    _refresh_cache(event_id, handlers)


def is_fully_processed(db: Session, event_id: str, handler_names: list[str]) -> bool:
    """True when every handler has a durable record for this event."""
    return set(handler_names).issubset(completed_handlers(db, event_id))
