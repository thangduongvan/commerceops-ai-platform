"""V4 (Asynchronous Processing): app/core/queue.py's publish_event must
never raise -- a queue outage degrades to "this event's side effects don't
run" (see docs/adr/ADR-005-async-processing.md), never to a failed order
request. Uses moto's SQS mock, so no LocalStack/Docker is required (same
"tests are fast and portable" philosophy as the SQLite-backed integration
tests and test_cache.py's real-failure-path unit tests).
"""

import json

import boto3
import pytest
from moto import mock_aws

from app.core import queue as queue_module
from app.core.config import settings


@pytest.fixture
def sqs_queue(monkeypatch):
    with mock_aws():
        client = boto3.client("sqs", region_name=settings.aws_region)
        client.create_queue(QueueName=settings.sqs_queue_name)

        # publish_event's module-level _sqs_client was built once, at import
        # time, against the real (unmocked) SQS endpoint. Point it at this
        # test's moto mock instead, and clear the URL cache so it re-resolves
        # against the queue just created above rather than a stale value
        # from another test.
        monkeypatch.setattr(queue_module, "_sqs_client", client)
        queue_module._queue_url_cache.clear()

        yield client


def test_publish_event_returns_event_id_and_round_trips_payload(sqs_queue):
    event_id = queue_module.publish_event("OrderCreated", {"order_id": 1})
    assert event_id is not None

    queue_url = sqs_queue.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    received = sqs_queue.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1)
    body = json.loads(received["Messages"][0]["Body"])

    assert body["event_id"] == event_id
    assert body["event_type"] == "OrderCreated"
    assert body["payload"] == {"order_id": 1}
    assert "occurred_at" in body


def test_publish_event_returns_none_when_queue_does_not_exist(monkeypatch):
    with mock_aws():
        # A fresh, empty moto region: no queue named settings.sqs_queue_name
        # exists, so get_queue_url raises -- publish_event must swallow it.
        monkeypatch.setattr(queue_module, "_sqs_client", boto3.client("sqs", region_name=settings.aws_region))
        queue_module._queue_url_cache.clear()

        assert queue_module.publish_event("OrderCreated", {"order_id": 1}) is None
