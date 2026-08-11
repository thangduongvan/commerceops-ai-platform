"""V4/V5: app/worker.py's poll-and-dispatch loop.

Uses moto's SQS mock (same technique as test_queue.py) plus an in-memory SQLite
session factory in place of Postgres, so nothing external needs to run.

V5 rewrote how failure is handled, so these tests are mostly about failure:
per-handler isolation, partial resumption on redelivery, poison messages, and
visibility extension. The V4 happy path is still here (dispatch to every
handler, then delete) because that behaviour did not change.
"""

import json
import uuid

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.worker as worker_module
from app.core import idempotency as idempotency_module
from app.core import queue as queue_module
from app.core.config import settings
from app.core.database import Base


@pytest.fixture(autouse=True)
def sqlite_sessions(monkeypatch):
    """Swap the worker's Postgres session factory for in-memory SQLite.

    SQLite enforces the (event_id, handler_name) unique constraint the same way
    Postgres does, so the idempotency behaviour under test is real.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(worker_module, "SessionLocal", factory)
    return factory


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """In-memory stand-in for the lease and the dedup cache.

    Redis isn't running in the test environment, and every real helper fails
    open, which would silently disable the very behaviour these tests assert on.
    """
    store: dict[str, object] = {}

    def get_json(key):
        return store.get(key)

    def set_json(key, value, ttl):
        store[key] = value

    def set_if_absent(key, ttl, value="1"):
        if key in store:
            return False
        store[key] = value
        return True

    def delete(*keys):
        for key in keys:
            store.pop(key, None)

    monkeypatch.setattr(idempotency_module, "cache_get_json", get_json)
    monkeypatch.setattr(idempotency_module, "cache_set_json", set_json)
    monkeypatch.setattr(idempotency_module, "cache_set_if_absent", set_if_absent)
    monkeypatch.setattr(idempotency_module, "cache_delete", delete)
    return store


@pytest.fixture(autouse=True)
def instant_handler_retries(monkeypatch):
    monkeypatch.setattr(settings, "worker_handler_retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "worker_handler_retry_max_delay_seconds", 0.0)


@pytest.fixture
def sqs_queue(monkeypatch):
    with mock_aws():
        client = boto3.client("sqs", region_name=settings.aws_region)
        client.create_queue(QueueName=settings.sqs_queue_name)
        client.create_queue(QueueName=settings.sqs_dlq_name)

        monkeypatch.setattr(worker_module, "_sqs_client", client)
        monkeypatch.setattr(queue_module, "_sqs_client", client)
        queue_module._queue_url_cache.clear()

        yield client


@pytest.fixture
def recorded_handlers(monkeypatch):
    """Four named handlers that record their calls, mirroring production's shape."""
    calls: list[tuple[str, str]] = []

    def make(name):
        return lambda event_type, payload: calls.append((name, event_type))

    monkeypatch.setattr(
        worker_module,
        "HANDLERS",
        {name: make(name) for name in ("notification", "email", "analytics", "search")},
    )
    return calls


def _send(client, event_type="OrderCreated", payload=None, event_id=None):
    queue_url = client.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    message = {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "payload": payload or {"order_id": 1},
        "occurred_at": "2026-01-01T00:00:00+00:00",
    }
    client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))
    return message["event_id"]


def _depth(client, queue_name):
    url = client.get_queue_url(QueueName=queue_name)["QueueUrl"]
    attributes = client.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return {
        "visible": int(attributes.get("ApproximateNumberOfMessages", 0)),
        "in_flight": int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
    }


# --- happy path (unchanged from V4) -----------------------------------------


def test_poll_once_dispatches_to_all_handlers_and_deletes_on_success(sqs_queue, recorded_handlers):
    _send(sqs_queue, event_type="OrderPaid", payload={"order_id": 42})

    received = worker_module.poll_once(wait_time_seconds=0)

    assert received == 1
    assert {name for name, _ in recorded_handlers} == {"notification", "email", "analytics", "search"}
    assert _depth(sqs_queue, settings.sqs_queue_name) == {"visible": 0, "in_flight": 0}


def test_a_failing_message_does_not_affect_others_in_the_same_batch(sqs_queue, monkeypatch):
    seen = []

    def selective(event_type, payload):
        if payload["order_id"] == 1:
            raise RuntimeError("this one is broken")
        seen.append(payload["order_id"])

    monkeypatch.setattr(worker_module, "HANDLERS", {"only": selective})
    _send(sqs_queue, payload={"order_id": 1})
    _send(sqs_queue, payload={"order_id": 2})

    worker_module.poll_once(max_messages=10, wait_time_seconds=0)

    assert seen == [2]


# --- per-handler failure isolation (V5) -------------------------------------


def test_a_failing_handler_leaves_the_message_for_redelivery(sqs_queue, monkeypatch):
    monkeypatch.setattr(
        worker_module,
        "HANDLERS",
        {"broken": lambda event_type, payload: (_ for _ in ()).throw(RuntimeError("boom"))},
    )
    _send(sqs_queue)

    worker_module.poll_once(wait_time_seconds=0)

    # Not deleted, so it becomes visible again after the visibility timeout --
    # SQS's own retry mechanism, and eventually its redrive to the DLQ.
    assert _depth(sqs_queue, settings.sqs_queue_name)["in_flight"] == 1


def test_successful_handlers_still_run_when_another_one_fails(sqs_queue, monkeypatch):
    calls = []

    monkeypatch.setattr(
        worker_module,
        "HANDLERS",
        {
            "good_one": lambda event_type, payload: calls.append("good_one"),
            "broken": lambda event_type, payload: (_ for _ in ()).throw(RuntimeError("boom")),
            "good_two": lambda event_type, payload: calls.append("good_two"),
        },
    )
    _send(sqs_queue)

    worker_module.poll_once(wait_time_seconds=0)

    # V4 aborted the whole fan-out on the first exception, so a handler
    # registered after the broken one never ran at all.
    assert calls == ["good_one", "good_two"]


def test_redelivery_reruns_only_the_handlers_that_failed(sqs_queue, monkeypatch):
    calls = []
    fail_broken = {"yes": True}

    def broken(event_type, payload):
        calls.append("broken")
        if fail_broken["yes"]:
            raise RuntimeError("still broken")

    monkeypatch.setattr(
        worker_module,
        "HANDLERS",
        {
            "good": lambda event_type, payload: calls.append("good"),
            "broken": broken,
        },
    )
    # One attempt per handler, so this test observes *which* handlers run rather
    # than how many times the retry ladder calls them (covered separately below).
    monkeypatch.setattr(settings, "worker_handler_retry_attempts", 1)

    event_id = _send(sqs_queue, event_id="partial-1")
    worker_module.poll_once(wait_time_seconds=0)
    assert calls == ["good", "broken"]

    # The redelivery SQS would eventually make after the visibility timeout.
    calls.clear()
    fail_broken["yes"] = False
    _send(sqs_queue, event_id=event_id)
    worker_module.poll_once(wait_time_seconds=0)

    # This is the V4 regression this whole redesign exists to fix. There, the
    # pre-emptive per-event marker made the redelivery look like a duplicate, so
    # the broken handler never got a second chance -- and had the marker not
    # existed, "good" would have run twice. Now: "good" is skipped (durably
    # recorded), "broken" is retried.
    assert calls == ["broken"]


def test_handler_retries_are_exhausted_before_giving_up(sqs_queue, monkeypatch):
    attempts = {"n": 0}

    def flaky(event_type, payload):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")

    monkeypatch.setattr(worker_module, "HANDLERS", {"flaky": flaky})
    monkeypatch.setattr(settings, "worker_handler_retry_attempts", 3)
    _send(sqs_queue)

    worker_module.poll_once(wait_time_seconds=0)

    # Recovered in-process, on the third attempt, so the message is deleted
    # rather than waiting a full visibility timeout for SQS to redeliver it.
    assert attempts["n"] == 3
    assert _depth(sqs_queue, settings.sqs_queue_name) == {"visible": 0, "in_flight": 0}


# --- idempotency ------------------------------------------------------------


def test_a_redelivered_completed_event_is_not_reprocessed(sqs_queue, recorded_handlers):
    event_id = _send(sqs_queue, event_id="dup-1")
    worker_module.poll_once(wait_time_seconds=0)
    assert len(recorded_handlers) == 4

    _send(sqs_queue, event_id=event_id)
    worker_module.poll_once(wait_time_seconds=0)

    # Same event_id, so no side effect runs a second time. The durable records
    # in processed_events -- not a TTL'd Redis key -- are what guarantee this.
    assert len(recorded_handlers) == 4


def test_the_lease_is_released_when_handlers_are_left_outstanding(sqs_queue, fake_redis, monkeypatch):
    monkeypatch.setattr(
        worker_module,
        "HANDLERS",
        {"broken": lambda event_type, payload: (_ for _ in ()).throw(RuntimeError("boom"))},
    )
    monkeypatch.setattr(settings, "worker_handler_retry_attempts", 1)
    _send(sqs_queue, event_id="incomplete-1")

    worker_module.poll_once(wait_time_seconds=0)

    # Holding a stale claim would make SQS's redelivery look like concurrent
    # work and get skipped, costing another full visibility timeout before the
    # failed handler is retried.
    assert "event_lease:incomplete-1" not in fake_redis


def test_a_message_already_in_flight_elsewhere_is_left_alone(sqs_queue, recorded_handlers, monkeypatch):
    monkeypatch.setattr(worker_module, "acquire_lease", lambda event_id: False)
    _send(sqs_queue)

    worker_module.poll_once(wait_time_seconds=0)

    # Another worker holds the claim, so this one does no work and does not
    # delete the message -- whoever holds the lease is responsible for it.
    assert recorded_handlers == []


# --- poison messages --------------------------------------------------------


def test_an_unparseable_body_goes_straight_to_the_dlq(sqs_queue, recorded_handlers):
    queue_url = sqs_queue.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    sqs_queue.send_message(QueueUrl=queue_url, MessageBody="this is not json")

    worker_module.poll_once(wait_time_seconds=0)

    # No handler ran, the message is off the main queue, and it's preserved in
    # the DLQ for inspection. Retrying it five times first would only add four
    # visibility timeouts of delay to a message that can never parse.
    assert recorded_handlers == []
    assert _depth(sqs_queue, settings.sqs_queue_name) == {"visible": 0, "in_flight": 0}
    assert _depth(sqs_queue, settings.sqs_dlq_name)["visible"] == 1


def test_a_body_missing_required_fields_is_also_treated_as_poison(sqs_queue, recorded_handlers):
    queue_url = sqs_queue.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    sqs_queue.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"event_id": "x"}))

    worker_module.poll_once(wait_time_seconds=0)

    assert recorded_handlers == []
    assert _depth(sqs_queue, settings.sqs_dlq_name)["visible"] == 1


# --- visibility extension ---------------------------------------------------


def test_visibility_is_extended_before_handlers_run(sqs_queue, recorded_handlers, monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_module,
        "_extend_visibility",
        lambda queue_url, receipt_handle, seconds: calls.append(seconds),
    )
    _send(sqs_queue)

    worker_module.poll_once(wait_time_seconds=0)

    # Without this, the in-process retry ladder can outlive the visibility
    # timeout and a second worker starts the same event mid-retry.
    assert calls == [settings.sqs_visibility_timeout_seconds]


def test_a_failed_visibility_extension_does_not_stop_processing(sqs_queue, recorded_handlers, monkeypatch):
    from botocore.exceptions import ClientError

    def failing_change_visibility(**kwargs):
        raise ClientError({"Error": {"Code": "InvalidParameterValue"}}, "ChangeMessageVisibility")

    monkeypatch.setattr(worker_module._sqs_client, "change_message_visibility", failing_change_visibility)
    _send(sqs_queue)

    worker_module.poll_once(wait_time_seconds=0)

    # Best-effort: the extension is an optimization against concurrent
    # reprocessing, and the idempotency records already prevent duplicate
    # effects, so losing it must not stall the queue.
    assert len(recorded_handlers) == 4


# --- resilience of the poll loop itself -------------------------------------


def test_receive_failure_returns_zero_instead_of_crashing(sqs_queue, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    def failing_receive(**kwargs):
        raise EndpointConnectionError(endpoint_url="http://sqs.invalid")

    monkeypatch.setattr(worker_module._sqs_client, "receive_message", failing_receive)

    assert worker_module.poll_once(wait_time_seconds=0) == 0


def test_run_worker_stops_after_the_requested_iterations(sqs_queue, recorded_handlers):
    _send(sqs_queue)

    # max_iterations exists so the otherwise-infinite loop is testable at all.
    worker_module.run_worker(max_iterations=1)

    assert len(recorded_handlers) == 4
