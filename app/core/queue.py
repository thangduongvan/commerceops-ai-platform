"""V4 (Asynchronous Processing): a thin SQS producer around boto3.

Order creation used to call notification (and, per this version's spec,
would otherwise grow to call analytics/email/search too) synchronously,
in-process. That chains every side effect onto the request's critical path.
Here, the request only has to publish one small event describing what
happened; a separate worker (app/worker.py) picks it up and fans out to all
four side effects off the request path.

Unlike app/core/cache.py, a publish failure here is NOT harmless: Redis is
never the source of truth, so a cache miss just costs latency, but a
message that never reaches the queue means that order's notification/
analytics/email/search side effects never happen at all. That trade-off is
accepted deliberately for V4 (never block or fail the order itself over a
side effect) and is called out explicitly in docs/adr/ADR-005-async-processing.md
as the reason V5 (reliability) and V11 (transactional outbox) exist.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

_sqs_client = boto3.client(
    "sqs",
    region_name=settings.aws_region,
    endpoint_url=settings.sqs_endpoint_url,
)

# Populated lazily by _queue_url() the first time each queue name is
# resolved, so a transient lookup failure doesn't get "cached" as broken.
_queue_url_cache: dict[str, str] = {}


def resolve_queue_url(queue_name: str) -> str:
    """Resolve a queue name to its URL, caching the result.

    Looking this up by name (rather than threading a pre-built URL through
    Terraform outputs / Compose env vars) means the exact same code works
    against LocalStack's fixed local account id and real AWS without any
    environment-specific URL ever being configured. Also used by
    app/worker.py, which issues its own receive_message/delete_message
    calls against a separate boto3 client (worker and app run as separate
    processes/ECS services), but resolving a queue name to a URL doesn't
    depend on which client later uses that URL.
    """
    if queue_name not in _queue_url_cache:
        response = _sqs_client.get_queue_url(QueueName=queue_name)
        _queue_url_cache[queue_name] = response["QueueUrl"]
    return _queue_url_cache[queue_name]


def publish_event(event_type: str, payload: dict[str, Any]) -> Optional[str]:
    """Best-effort publish of a business event to the order-events queue.

    Returns the generated event_id on success, or None if the publish
    failed. Never raises: a queue outage degrades to "this order's async
    side effects are skipped", not to a failed order request.
    """
    event_id = str(uuid.uuid4())
    message = {
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        queue_url = resolve_queue_url(settings.sqs_queue_name)
        _sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))
    except (BotoCoreError, ClientError):
        logger.warning(
            "publish_event failed for event_type=%s, side effects will not run for this event",
            event_type,
            exc_info=True,
        )
        return None
    return event_id
