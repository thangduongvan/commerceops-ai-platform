"""V4/V5: the order-events consumer process.

Long-polls the queue app/core/queue.py's publish_event() sends to, and fans
each event out to the four side effects the learning project spec lists for
order creation — Notification, Analytics, Email, Search indexing — instead of
app/order/service.py calling them in-process.

V5 (Reliability) changed how failure is handled, not what the handlers do:

* **Per-handler isolation.** Each handler is retried, recorded, and can fail
  independently. In V4 one broken handler forced the whole message (all four
  side effects) to be redelivered and re-run.
* **Exponential backoff in-process**, bounded so the whole ladder fits inside
  the visibility timeout, with SQS redelivery as the outer, slower retry.
* **Durable per-handler idempotency** (app/core/idempotency.py) instead of one
  pre-emptive Redis key per event — which, being set before the handlers ran,
  actively lost side effects on partial failure.
* **Poison messages** go straight to the DLQ rather than burning five
  redeliveries on a body that can never parse.

See docs/adr/ADR-006-reliability.md.

Run directly:

    python -m app.worker

Docker Compose's "worker" service and the ECS worker service
(infra/modules/ecs) both just run this module as their command — the exact
same code, same handlers, whether running against LocalStack or real SQS.
"""

import json
import logging
from typing import Callable, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.analytics.service import record_event
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.idempotency import (
    acquire_lease,
    completed_handlers,
    record_handler_done,
    release_lease,
)
from app.core.queue import resolve_queue_url, sqs_client_config
from app.core.reliability import retry_with_backoff
from app.notification.service import send_email, send_notification
from app.search.service import index_event

logger = logging.getLogger("commerceops.worker")

LONG_POLL_SECONDS = 20

# The receive client needs a read_timeout longer than the long poll it's
# waiting out, or *every* receive_message would abort at the socket level
# after settings.sqs_read_timeout_seconds and the worker would spin. This is
# the classic long-polling footgun, and the reason the worker builds its own
# client rather than reusing app/core/queue.py's producer client.
_sqs_client = boto3.client(
    "sqs",
    region_name=settings.aws_region,
    endpoint_url=settings.sqs_endpoint_url,
    config=sqs_client_config(read_timeout=LONG_POLL_SECONDS + 10),
)

# Every order event fans out to all four of these, uniformly -- the spec's
# architecture diagram fans "Order Event" straight out to Notification /
# Analytics / Email / Search with no per-event-type routing. Per-event-type
# routing (e.g. only email on OrderPaid, not OrderCancelled) is a
# reasonable next step, deferred to V8 (Event-Driven Architecture) once
# there's an actual reason to add it.
#
# V5: keyed by name, because the name is what gets recorded in
# processed_events as "this specific side effect already happened."
HANDLERS: dict[str, Callable[[str, dict], None]] = {
    "notification": send_notification,
    "email": send_email,
    "analytics": record_event,
    "search": index_event,
}


class PoisonMessage(Exception):
    """A message that can never succeed no matter how often it's retried."""


def _extend_visibility(queue_url: str, receipt_handle: str, seconds: int) -> None:
    """Hold the in-flight window open while we retry handlers.

    Without this, the in-process retry ladder can outlive the visibility
    timeout, SQS makes the message visible again mid-retry, and a second
    worker starts the same event concurrently — turning a retry into a
    duplicate. Best-effort: if the call fails we keep going, since the
    idempotency records still prevent duplicate effects.
    """
    try:
        _sqs_client.change_message_visibility(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=seconds,
        )
    except (BotoCoreError, ClientError):
        logger.warning("change_message_visibility failed, continuing anyway", exc_info=True)


def _send_to_dlq(body: str) -> None:
    """Park an unprocessable message in the DLQ immediately.

    A body that doesn't parse will not parse on the fifth attempt either.
    Letting the redrive policy get it there costs four pointless redeliveries
    and four visibility timeouts of delay; deleting it outright would lose the
    evidence. So: straight to the DLQ, where the dlq-not-empty alarm and
    `python -m app.dlq inspect` will surface it.
    """
    try:
        dlq_url = resolve_queue_url(settings.sqs_dlq_name)
        _sqs_client.send_message(QueueUrl=dlq_url, MessageBody=body)
    except (BotoCoreError, ClientError):
        logger.exception("could not move poison message to the DLQ, leaving it on the queue")
        raise


def _parse(body: str) -> tuple[str, str, dict]:
    try:
        message = json.loads(body)
        return message["event_id"], message["event_type"], message["payload"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PoisonMessage(str(exc)) from exc


def process_event(event_id: str, event_type: str, payload: dict) -> list[str]:
    """Run every handler that hasn't already completed for this event.

    Returns the names of handlers that are still outstanding, so the caller
    knows whether the message can be deleted. Each handler is independent: one
    failing does not stop, skip, or undo the others. That's the failure
    isolation the V4 design lacked — there, handler 3 raising meant handlers 1
    and 2 ran again on redelivery (duplicate effects) and handler 4 never ran
    at all.
    """
    db = SessionLocal()
    try:
        already_done = completed_handlers(db, event_id)
        if already_done:
            logger.info(
                "event_id=%s resuming, handlers already done: %s",
                event_id,
                sorted(already_done),
            )

        outstanding: list[str] = []
        for name, handler in HANDLERS.items():
            if name in already_done:
                continue
            try:
                retry_with_backoff(
                    lambda h=handler: h(event_type, payload),
                    attempts=settings.worker_handler_retry_attempts,
                    base_delay=settings.worker_handler_retry_base_delay_seconds,
                    multiplier=settings.retry_multiplier,
                    max_delay=settings.worker_handler_retry_max_delay_seconds,
                    name=f"handler:{name}:{event_type}",
                )
            except Exception:
                logger.exception(
                    "handler=%s failed for event_id=%s, leaving it for redelivery",
                    name,
                    event_id,
                )
                outstanding.append(name)
                continue
            record_handler_done(db, event_id, event_type, name)

        return outstanding
    finally:
        db.close()


def _handle_message(queue_url: str, message: dict) -> None:
    body = message["Body"]
    receipt_handle = message["ReceiptHandle"]

    try:
        event_id, event_type, payload = _parse(body)
    except PoisonMessage as exc:
        logger.error("poison message %s (%s), moving straight to the DLQ", message.get("MessageId"), exc)
        _send_to_dlq(body)
        _sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    if not acquire_lease(event_id):
        # Another worker holds the claim right now (this is a redelivery that
        # overtook the original). Leave the message alone rather than
        # duplicating work; whoever holds the lease will delete it.
        logger.info("event_id=%s already in flight on another worker, skipping", event_id)
        return

    _extend_visibility(
        queue_url,
        receipt_handle,
        settings.sqs_visibility_timeout_seconds,
    )

    outstanding = process_event(event_id, event_type, payload)
    if outstanding:
        # Deliberately not deleted: the message becomes visible again after the
        # visibility timeout, and the next delivery re-runs *only* these
        # handlers, because the successful ones now have durable records.
        # After settings.sqs_max_receive_count deliveries SQS moves it to the
        # DLQ on its own.
        #
        # Release the lease first, though — we've stopped working on this event,
        # and holding a stale claim would make the redelivery skip it as
        # "already in flight" and wait out another whole visibility timeout.
        release_lease(event_id)
        logger.warning(
            "event_id=%s incomplete, outstanding handlers=%s, awaiting redelivery",
            event_id,
            outstanding,
        )
        return

    _sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


def poll_once(max_messages: int = 10, wait_time_seconds: int = LONG_POLL_SECONDS) -> int:
    """Receive up to one batch of messages, dispatch, and delete on success.

    Returns the number of messages received in this batch (not necessarily
    processed successfully). One message's failure never affects the others in
    the batch.
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
            _handle_message(queue_url, message)
        except Exception:
            # The last line of defence. Anything reaching here (an unexpected
            # boto error, a DB outage) leaves the message in flight for
            # redelivery rather than killing the poll loop and stalling the
            # queue for every other message.
            logger.exception(
                "unexpected failure handling message %s, leaving it for redelivery",
                message.get("MessageId"),
            )

    return len(messages)


def run_worker(max_iterations: Optional[int] = None) -> None:
    logger.info("worker starting, polling queue=%s", settings.sqs_queue_name)
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        poll_once()
        iterations += 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker()
