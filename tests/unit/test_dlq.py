"""V5 (Reliability): app/dlq.py, the dead-letter queue operator tooling.

moto-backed, so no LocalStack or AWS is needed. The behaviours worth pinning
down are the ones that would quietly lose business events if they were wrong:
inspection must not consume messages, and redrive must send before it deletes.
"""

import json
import uuid

import boto3
import pytest
from moto import mock_aws

from app import dlq as dlq_module
from app.core import queue as queue_module
from app.core.config import settings


@pytest.fixture
def queues(monkeypatch):
    with mock_aws():
        client = boto3.client("sqs", region_name=settings.aws_region)
        client.create_queue(QueueName=settings.sqs_queue_name)
        client.create_queue(QueueName=settings.sqs_dlq_name)

        monkeypatch.setattr(dlq_module, "_sqs_client", client)
        monkeypatch.setattr(queue_module, "_sqs_client", client)
        queue_module._queue_url_cache.clear()

        yield client


def _fill_dlq(client, count: int) -> list[str]:
    url = client.get_queue_url(QueueName=settings.sqs_dlq_name)["QueueUrl"]
    event_ids = []
    for _ in range(count):
        event_id = str(uuid.uuid4())
        event_ids.append(event_id)
        client.send_message(
            QueueUrl=url,
            MessageBody=json.dumps(
                {
                    "event_id": event_id,
                    "event_type": "OrderCreated",
                    "payload": {"order_id": 1},
                    "occurred_at": "2026-01-01T00:00:00+00:00",
                }
            ),
        )
    return event_ids


def _depth(client, queue_name: str) -> int:
    url = client.get_queue_url(QueueName=queue_name)["QueueUrl"]
    attributes = client.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    return int(attributes.get("ApproximateNumberOfMessages", 0))


# --- inspect ----------------------------------------------------------------


def test_inspect_reports_zero_for_an_empty_dlq(queues):
    result = dlq_module.inspect()

    assert result["queue"] == settings.sqs_dlq_name
    assert result["visible"] == 0
    assert result["sample"] == []


def test_inspect_reports_depth_and_samples_bodies(queues):
    event_ids = _fill_dlq(queues, 3)

    result = dlq_module.inspect(sample=3)

    assert result["visible"] == 3
    assert len(result["sample"]) == 3
    assert {body["event_id"] for body in result["sample"]} == set(event_ids)


def test_inspect_does_not_consume_the_messages_it_samples(queues):
    _fill_dlq(queues, 3)

    dlq_module.inspect(sample=3)

    # Zero visibility timeout on the peek: an inspection that hid messages from
    # everyone else for 30 seconds would make the DLQ harder to work with the
    # more you looked at it.
    assert _depth(queues, settings.sqs_dlq_name) == 3
    assert dlq_module.inspect(sample=3)["visible"] == 3


def test_inspect_can_skip_bodies_entirely(queues):
    _fill_dlq(queues, 2)

    result = dlq_module.inspect(sample=0)

    assert result["visible"] == 2
    assert result["sample"] == []


def test_inspect_surfaces_unparseable_bodies_rather_than_crashing(queues):
    url = queues.get_queue_url(QueueName=settings.sqs_dlq_name)["QueueUrl"]
    queues.send_message(QueueUrl=url, MessageBody="not json at all")

    result = dlq_module.inspect(sample=1)

    # Poison messages are exactly what lands here, so the tool that inspects the
    # DLQ must not itself choke on one.
    assert result["sample"][0]["unparseable_body"] == "not json at all"


# --- redrive ----------------------------------------------------------------


def test_redrive_moves_messages_back_to_the_main_queue(queues):
    event_ids = _fill_dlq(queues, 4)

    result = dlq_module.redrive()

    assert result == {"moved": 4, "failed": 0}
    assert _depth(queues, settings.sqs_dlq_name) == 0
    assert _depth(queues, settings.sqs_queue_name) == 4

    main_url = queues.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    received = queues.receive_message(QueueUrl=main_url, MaxNumberOfMessages=10)["Messages"]
    assert {json.loads(m["Body"])["event_id"] for m in received} == set(event_ids)


def test_redrive_respects_the_max_limit(queues):
    _fill_dlq(queues, 8)

    result = dlq_module.redrive(max_messages=3)

    # Draining in controlled batches matters when the DLQ holds thousands: a
    # full redrive into a dependency that's only just recovered can knock it
    # straight back over.
    assert result["moved"] == 3
    assert _depth(queues, settings.sqs_dlq_name) == 5
    assert _depth(queues, settings.sqs_queue_name) == 3


def test_redrive_on_an_empty_dlq_is_a_no_op(queues):
    assert dlq_module.redrive() == {"moved": 0, "failed": 0}


def test_a_failed_send_leaves_the_message_in_the_dlq(queues, monkeypatch):
    from botocore.exceptions import ClientError

    _fill_dlq(queues, 2)

    def failing_send(**kwargs):
        raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "SendMessage")

    monkeypatch.setattr(dlq_module._sqs_client, "send_message", failing_send)
    result = dlq_module.redrive()

    # Send first, delete only on success. The reverse order would lose the
    # message outright; this way the worst case is a duplicate, which
    # app/core/idempotency.py already handles.
    assert result["moved"] == 0
    assert result["failed"] == 2

    # Still on the DLQ, though counted as in-flight rather than visible: they
    # were received and never deleted, so they reappear after the visibility
    # timeout. Not lost, which is the only thing that matters here.
    depth = dlq_module.queue_depth(settings.sqs_dlq_name)
    assert depth["visible"] + depth["in_flight"] == 2


def test_redrive_preserves_message_bodies_exactly(queues):
    url = queues.get_queue_url(QueueName=settings.sqs_dlq_name)["QueueUrl"]
    body = json.dumps({"event_id": "keep-me", "event_type": "OrderPaid", "payload": {"order_id": 9}})
    queues.send_message(QueueUrl=url, MessageBody=body)

    dlq_module.redrive()

    main_url = queues.get_queue_url(QueueName=settings.sqs_queue_name)["QueueUrl"]
    received = queues.receive_message(QueueUrl=main_url, MaxNumberOfMessages=1)["Messages"][0]
    # Same event_id after the round trip, which is what lets the redriven
    # message be deduplicated against whatever already succeeded for it.
    assert received["Body"] == body


# --- queue depth helper -----------------------------------------------------


def test_queue_depth_reports_visible_and_in_flight(queues):
    _fill_dlq(queues, 2)

    assert dlq_module.queue_depth(settings.sqs_dlq_name) == {"visible": 2, "in_flight": 0}


# --- CLI --------------------------------------------------------------------


def test_purge_refuses_without_explicit_confirmation(queues, capsys):
    _fill_dlq(queues, 2)

    exit_code = dlq_module.main(["purge"])

    # Purging is unrecoverable, so it should be awkward to do by accident.
    assert exit_code == 1
    assert "refusing to purge" in capsys.readouterr().out
    assert _depth(queues, settings.sqs_dlq_name) == 2


def test_cli_inspect_prints_json(queues, capsys):
    _fill_dlq(queues, 1)

    assert dlq_module.main(["inspect", "--sample", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["visible"] == 1


def test_cli_redrive_prints_json(queues, capsys):
    _fill_dlq(queues, 2)

    assert dlq_module.main(["redrive", "--max", "2"]) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": 2, "failed": 0}
