"""V4 (Asynchronous Processing): the order-events consumer process.

Long-polls the queue app/core/queue.py's publish_event() sends to, and fans
each event out to all four side effects the learning project spec lists for
order creation — Notification, Analytics, Email, Search indexing — instead
of app/order/service.py calling them in-process. See the architecture
diagram and the "what happens when a handler fails / a message is
redelivered" answers in docs/adr/ADR-005-async-processing.md.

Run directly:

    python -m app.worker

Docker Compose's "worker" service and the ECS worker service
(infra/modules/ecs) both just run this module as their command — the exact
same code, same handlers, whether running against LocalStack or real SQS.
"""

import json
import logging
from typing import Callable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.analytics.service import record_event
from app.core.cache import mark_event_processed
from app.core.config import settings
from app.core.queue import resolve_queue_url
from app.notification.service import send_email, send_notification
from app.search.service import index_event

logger = logging.getLogger("commerceops.worker")

_sqs_client = boto3.client(
    "sqs",
    region_name=settings.aws_region,
    endpoint_url=settings.sqs_endpoint_url,
)

# Every order event fans out to all four of these, uniformly -- the spec's
# architecture diagram fans "Order Event" straight out to Notification /
# Analytics / Email / Search with no per-event-type routing. Per-event-type
# routing (e.g. only email on OrderPaid, not OrderCancelled) is a
# reasonable next step, deferred to V8 (Event-Driven Architecture) once
# there's an actual reason to add it.
HANDLERS: list[Callable[[str, dict], None]] = [
    send_notification,
    send_email,
    record_event,
    index_event,
]


def _process_message(body: str) -> None:
    message = json.loads(body)
    event_id = message["event_id"]
    event_type = message["event_type"]
    payload = message["payload"]

    if not mark_event_processed(event_id, settings.sqs_idempotency_ttl_seconds):
        logger.info("skipping duplicate delivery of event_id=%s event_type=%s", event_id, event_type)
        return

    for handler in HANDLERS:
        handler(event_type, payload)


def poll_once(max_messages: int = 10, wait_time_seconds: int = 20) -> int:
    """Receive up to one batch of messages, dispatch, and delete on success.

    Returns the number of messages received in this batch (not necessarily
    processed successfully). If a handler raises for a given message, that
    message is simply left un-deleted -- it becomes visible again once the
    queue's visibility timeout (settings.sqs_visibility_timeout_seconds)
    elapses, which is SQS's own retry mechanism (no manual backoff loop
    here; exponential backoff is V5's job). After
    settings.sqs_max_receive_count delivery attempts, the queue's redrive
    policy moves the message to the DLQ automatically. Other messages in
    the same batch are unaffected by one message's failure.
    """
    queue_url = resolve_queue_url(settings.sqs_queue_name)
    try:
        response = _sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_time_seconds,
        )
    except (BotoCoreError, ClientError):
        logger.warning("receive_message failed, will retry on the next poll", exc_info=True)
        return 0

    messages = response.get("Messages", [])
    for message in messages:
        try:
            _process_message(message["Body"])
        except Exception:
            logger.exception(
                "failed to process message %s, leaving it for redelivery",
                message.get("MessageId"),
            )
            continue
        _sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])

    return len(messages)


def run_worker() -> None:
    logger.info("worker starting, polling queue=%s", settings.sqs_queue_name)
    while True:
        poll_once(wait_time_seconds=20)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker()
