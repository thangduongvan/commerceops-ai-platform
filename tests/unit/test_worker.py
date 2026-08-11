"""V4 (Asynchronous Processing): app/worker.py's poll-and-dispatch loop.

Uses moto's SQS mock (same technique as test_queue.py) plus a monkeypatched
fake for mark_event_processed: Redis isn't running in the test environment,
and the real helper fails open ("not a duplicate") whenever Redis is
unreachable (see app/core/cache.py), so a fake in-memory version is used
here to actually exercise the dedup path -- the same technique
test_product_cache.py's FakeCache uses for other Redis-dependent behavior.
"""

import json
import uuid

import boto3
import pytest
from moto import mock_aws

import app.worker as worker_module
from app.core import queue as queue_module
from app.core.config import settings


@pytest.fixture
def sqs_queue(monkeypatch):
    with mock_aws():
        client = boto3.client("sqs", region_name=settings.aws_region)
        client.create_queue(QueueName=settings.sqs_queue_name)

        monkeypatch.setattr(worker_module, "_sqs_client", client)
        monkeypatch.setattr(queue_module, "_sqs_client", client)
        queue_module._queue_url_cache.clear()

        yield client


@pytest.fixture
def fake_handlers(monkeypatch):
    calls = []
    monkeypatch.setattr(worker_module, "HANDLERS", [lambda event_type, payload: calls.append((event_type, payload))])
    return calls


@pytest.fixture(autouse=True)
def always_fresh_idempotency(monkeypatch):
    """Default for every test in this file: every event_id looks new. The
    one test that actually exercises dedup overrides this with its own fake.
    """
    monkeypatch.setattr(worker_module, "mark_event_processed", lambda event_id, ttl: True)


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


def test_poll_once_dispatches_to_all_handlers_and_deletes_on_success(sqs_queue, fake_handlers):
    _send(sqs_queue, event_type="OrderPaid", payload={"order_id": 42})

    received = worker_module.poll_once(wait_time_seconds=0)

    assert received == 1
    assert fake_handlers == [("OrderPaid", {"order_id": 42})]

    queue_url = sqs_queue.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    attrs = sqs_queue.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    assert attrs["ApproximateNumberOfMessages"] == "0"
    assert attrs["ApproximateNumberOfMessagesNotVisible"] == "0"


def test_poll_once_leaves_message_for_redelivery_on_handler_failure(sqs_queue, monkeypatch):
    def _raise(event_type, payload):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(worker_module, "HANDLERS", [_raise])
    _send(sqs_queue)

    received = worker_module.poll_once(wait_time_seconds=0)
    assert received == 1

    queue_url = sqs_queue.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    attrs = sqs_queue.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    # Not deleted: still counted as in-flight (received but never
    # acknowledged), so it becomes visible again once the queue's
    # visibility timeout elapses -- SQS's own retry mechanism, no manual
    # backoff loop needed here.
    assert attrs["ApproximateNumberOfMessagesNotVisible"] == "1"


def test_poll_once_skips_a_redelivered_duplicate_event_id(sqs_queue, fake_handlers, monkeypatch):
    seen: set[str] = set()

    def fake_mark_processed(event_id: str, ttl: int) -> bool:
        if event_id in seen:
            return False
        seen.add(event_id)
        return True

    monkeypatch.setattr(worker_module, "mark_event_processed", fake_mark_processed)

    event_id = _send(sqs_queue, event_id="dup-1")
    worker_module.poll_once(wait_time_seconds=0)
    assert len(fake_handlers) == 1

    # Simulates SQS's at-least-once delivery redelivering the same logical
    # event (same event_id) a second time.
    _send(sqs_queue, event_id=event_id)
    worker_module.poll_once(wait_time_seconds=0)
    assert len(fake_handlers) == 1  # not re-dispatched
