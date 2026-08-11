"""V5 (Reliability): operator tooling for the dead-letter queue.

V4 built the DLQ and an alarm for it, but the only ways to actually look
inside or drain it were the AWS console and raw `aws sqs` calls. A DLQ you
can't inspect is a data graveyard: the messages are retained for 14 days and
then silently disappear.

    python -m app.dlq inspect              # how many, and what do they look like
    python -m app.dlq redrive              # send them back to the main queue
    python -m app.dlq redrive --max 10     # ...a few at a time
    python -m app.dlq purge --yes          # give up on them (destructive)

Inspect is non-destructive: messages are received with a zero visibility
timeout so they stay immediately available to everyone else.

Redrive order matters — send to the main queue *first*, delete from the DLQ
only once that succeeded. The reverse order can lose a message if the send
fails, and at-least-once (a possible duplicate, which
app/core/idempotency.py deduplicates) is strictly better than at-most-once
when the payload is a business event.

Works unchanged against LocalStack and real SQS, same as everything else
built on app/core/queue.py.
"""

import argparse
import json
import logging
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.queue import resolve_queue_url, sqs_client_config

logger = logging.getLogger("commerceops.dlq")

_sqs_client = boto3.client(
    "sqs",
    region_name=settings.aws_region,
    endpoint_url=settings.sqs_endpoint_url,
    config=sqs_client_config(),
)


def queue_depth(queue_name: str) -> dict[str, int]:
    """Approximate message counts for a queue."""
    url = resolve_queue_url(queue_name)
    attributes = _sqs_client.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]
    return {
        "visible": int(attributes.get("ApproximateNumberOfMessages", 0)),
        "in_flight": int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
    }


def inspect(sample: int = 5) -> dict:
    """Report DLQ depth plus a sample of bodies, without consuming anything."""
    url = resolve_queue_url(settings.sqs_dlq_name)
    depth = queue_depth(settings.sqs_dlq_name)

    bodies = []
    if sample > 0:
        response = _sqs_client.receive_message(
            QueueUrl=url,
            MaxNumberOfMessages=min(sample, 10),
            # Zero, so peeking doesn't hide these messages from anyone else --
            # inspection must not perturb the thing being inspected.
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
        )
        for message in response.get("Messages", []):
            try:
                bodies.append(json.loads(message["Body"]))
            except ValueError:
                bodies.append({"unparseable_body": message["Body"][:500]})

    return {"queue": settings.sqs_dlq_name, **depth, "sample": bodies}


def redrive(max_messages: int = 100) -> dict:
    """Move messages from the DLQ back onto the main queue.

    Run this only after fixing whatever made them fail: a message that failed
    five times will fail five more, land back in the DLQ, and you'll have
    achieved nothing but noise on the alarm.
    """
    dlq_url = resolve_queue_url(settings.sqs_dlq_name)
    main_url = resolve_queue_url(settings.sqs_queue_name)

    moved = 0
    failed = 0
    while moved + failed < max_messages:
        response = _sqs_client.receive_message(
            QueueUrl=dlq_url,
            MaxNumberOfMessages=min(10, max_messages - moved - failed),
            WaitTimeSeconds=1,
        )
        messages = response.get("Messages", [])
        if not messages:
            break

        for message in messages:
            try:
                _sqs_client.send_message(QueueUrl=main_url, MessageBody=message["Body"])
            except (BotoCoreError, ClientError):
                logger.exception("failed to re-send message %s, leaving it in the DLQ", message.get("MessageId"))
                failed += 1
                continue
            _sqs_client.delete_message(QueueUrl=dlq_url, ReceiptHandle=message["ReceiptHandle"])
            moved += 1

    return {"moved": moved, "failed": failed}


def purge() -> dict:
    """Discard everything in the DLQ. Destructive and unrecoverable."""
    _sqs_client.purge_queue(QueueUrl=resolve_queue_url(settings.sqs_dlq_name))
    return {"purged": settings.sqs_dlq_name}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Inspect and drain the order-events dead-letter queue")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="show DLQ depth and a sample of messages")
    inspect_parser.add_argument("--sample", type=int, default=5, help="how many bodies to print (0 for counts only)")

    redrive_parser = subparsers.add_parser("redrive", help="move DLQ messages back to the main queue")
    redrive_parser.add_argument("--max", type=int, default=100, dest="max_messages")

    purge_parser = subparsers.add_parser("purge", help="permanently discard all DLQ messages")
    purge_parser.add_argument("--yes", action="store_true", help="required confirmation")

    args = parser.parse_args(argv)

    if args.command == "inspect":
        print(json.dumps(inspect(args.sample), indent=2))
    elif args.command == "redrive":
        print(json.dumps(redrive(args.max_messages), indent=2))
    elif args.command == "purge":
        if not args.yes:
            print("refusing to purge without --yes")
            return 1
        print(json.dumps(purge(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
