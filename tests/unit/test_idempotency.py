"""V5 (Reliability): app/core/idempotency.py.

Backed by an in-memory SQLite database (same technique as the integration
tests) plus a fake Redis, so the two layers can be exercised independently --
which is the whole point of the design: Postgres is the authority, Redis is a
cache in front of it, and killing Redis must slow things down rather than break
them.

Note that SQLite enforces the (event_id, handler_name) unique constraint just
like Postgres does, so the "concurrent worker won the race" path is genuinely
exercised here rather than mocked.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import idempotency
from app.core.database import Base
from app.core.models import ProcessedEvent

HANDLERS = ["notification", "email", "analytics", "search"]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


class FakeRedis:
    """Minimal stand-in supporting the three operations this module uses."""

    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, object] = {}
        self.broken = broken
        self.get_calls = 0

    def get_json(self, key):
        self.get_calls += 1
        if self.broken:
            return None
        return self.store.get(key)

    def set_json(self, key, value, ttl):
        if self.broken:
            return
        self.store[key] = value

    def set_if_absent(self, key, ttl, value="1"):
        if self.broken:
            return True  # fail open, as the real helper does
        if key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, *keys):
        if self.broken:
            return
        for key in keys:
            self.store.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(idempotency, "cache_get_json", fake.get_json)
    monkeypatch.setattr(idempotency, "cache_set_json", fake.set_json)
    monkeypatch.setattr(idempotency, "cache_set_if_absent", fake.set_if_absent)
    monkeypatch.setattr(idempotency, "cache_delete", fake.delete)
    return fake


@pytest.fixture
def broken_redis(monkeypatch):
    fake = FakeRedis(broken=True)
    monkeypatch.setattr(idempotency, "cache_get_json", fake.get_json)
    monkeypatch.setattr(idempotency, "cache_set_json", fake.set_json)
    monkeypatch.setattr(idempotency, "cache_set_if_absent", fake.set_if_absent)
    monkeypatch.setattr(idempotency, "cache_delete", fake.delete)
    return fake


# --- leases -----------------------------------------------------------------


def test_first_lease_is_granted_and_the_second_is_refused(redis):
    assert idempotency.acquire_lease("event-1") is True
    # A redelivery arriving while the first worker is still processing must not
    # start a second, concurrent run of the same event.
    assert idempotency.acquire_lease("event-1") is False


def test_leases_are_per_event(redis):
    assert idempotency.acquire_lease("event-1") is True
    assert idempotency.acquire_lease("event-2") is True


def test_releasing_a_lease_lets_the_next_delivery_claim_it(redis):
    assert idempotency.acquire_lease("event-1") is True
    idempotency.release_lease("event-1")

    # A worker that gave up with handlers outstanding must hand the claim back,
    # or SQS's redelivery is skipped as concurrent work and the failed handlers
    # wait out another full visibility timeout for nothing.
    assert idempotency.acquire_lease("event-1") is True


def test_lease_fails_open_when_redis_is_down(broken_redis):
    # The lease is an optimization, never a correctness mechanism. If it
    # blocked on a Redis outage the worker would simply stop making progress;
    # duplicate work is caught by the durable records below instead.
    assert idempotency.acquire_lease("event-1") is True
    assert idempotency.acquire_lease("event-1") is True


# --- durable per-handler records --------------------------------------------


def test_no_handlers_are_recorded_for_a_brand_new_event(db, redis):
    assert idempotency.completed_handlers(db, "event-new") == set()


def test_recording_a_handler_makes_it_visible_and_durable(db, redis):
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")

    assert idempotency.completed_handlers(db, "event-1") == {"email"}

    rows = db.execute(
        select(ProcessedEvent.handler_name).where(ProcessedEvent.event_id == "event-1")
    ).scalars().all()
    assert rows == ["email"]


def test_handlers_are_recorded_independently(db, redis):
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "analytics")

    # Per-handler granularity is what lets a redelivery re-run only what failed.
    # A single per-event marker cannot express "email done, search not".
    assert idempotency.completed_handlers(db, "event-1") == {"email", "analytics"}
    assert not idempotency.is_fully_processed(db, "event-1", HANDLERS)


def test_is_fully_processed_only_once_every_handler_is_recorded(db, redis):
    for handler in HANDLERS[:-1]:
        idempotency.record_handler_done(db, "event-1", "OrderCreated", handler)
    assert not idempotency.is_fully_processed(db, "event-1", HANDLERS)

    idempotency.record_handler_done(db, "event-1", "OrderCreated", HANDLERS[-1])
    assert idempotency.is_fully_processed(db, "event-1", HANDLERS)


def test_recording_the_same_handler_twice_is_a_no_op(db, redis):
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")

    rows = db.execute(
        select(ProcessedEvent).where(ProcessedEvent.event_id == "event-1")
    ).scalars().all()
    # The unique constraint -- not the read in completed_handlers, which can
    # always be raced -- is what actually enforces this. A concurrent worker
    # losing the race gets an IntegrityError, which is a success, not an error.
    assert len(rows) == 1


def test_the_same_handler_can_be_recorded_for_different_events(db, redis):
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")
    idempotency.record_handler_done(db, "event-2", "OrderPaid", "email")

    assert idempotency.completed_handlers(db, "event-1") == {"email"}
    assert idempotency.completed_handlers(db, "event-2") == {"email"}


# --- Redis as a cache, Postgres as the authority ----------------------------


def test_completed_handlers_serves_repeat_reads_from_the_cache(db, redis):
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")

    # Delete the row behind the cache's back: a subsequent read that still says
    # "email" proves it came from Redis rather than the database.
    db.query(ProcessedEvent).delete()
    db.commit()

    assert idempotency.completed_handlers(db, "event-1") == {"email"}


def test_completed_handlers_falls_back_to_postgres_when_redis_is_down(db, broken_redis):
    db.add(ProcessedEvent(event_id="event-1", handler_name="search", event_type="OrderCreated"))
    db.commit()

    # Slower (a query per read instead of a cache hit), never wrong. This is the
    # exact scenario V4's Redis-only guard got wrong: Redis being unavailable is
    # correlated with the failures that cause redeliveries in the first place.
    assert idempotency.completed_handlers(db, "event-1") == {"search"}


def test_records_survive_a_redis_outage_entirely(db, broken_redis):
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "analytics")

    assert idempotency.completed_handlers(db, "event-1") == {"email", "analytics"}


def test_cache_is_refreshed_immediately_after_recording(db, redis):
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "email")
    idempotency.record_handler_done(db, "event-1", "OrderCreated", "search")

    # Waiting for the TTL would let a redelivery moments later re-run a handler
    # that just completed.
    assert set(redis.store["event_handlers_done:event-1"]) == {"email", "search"}
