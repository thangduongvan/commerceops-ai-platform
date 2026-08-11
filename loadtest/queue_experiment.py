"""V4 (Asynchronous Processing) backpressure experiment.

Produces synthetic OrderCreated-shaped events directly against the
order-events queue (LocalStack locally, real SQS in AWS) much faster than a
single worker can consume them, so the backlog (ApproximateNumberOfMessages
Visible) is visible growing -- then draining once more workers are added.
This is deliberately a standalone boto3 script, not Locust: it isn't
generating HTTP traffic against the app, it's talking to the queue
directly, mirroring how app/core/queue.py and app/worker.py do it.

See docs/deployment.md's "V4: Asynchronous Processing" section for the full
staged run:

    # 1. Produce a burst of 5,000 events as fast as possible
    python loadtest/queue_experiment.py produce --count 5000

    # 2. Watch the backlog while a single slow worker (~500/sec) drains it
    python loadtest/queue_experiment.py consume --delay-ms 2
    python loadtest/queue_experiment.py depth --watch

    # 3. Scale workers (docker compose --scale worker=N / Auto Scaling in
    #    AWS) and watch the backlog drain faster.
"""

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone

import boto3


def _client():
    return boto3.client(
        "sqs",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        endpoint_url=os.environ.get("SQS_ENDPOINT_URL"),
    )


def _queue_url(client, queue_name: str) -> str:
    return client.get_queue_url(QueueName=queue_name)["QueueUrl"]


def _fake_order_event() -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderCreated",
        "payload": {
            "order_id": str(uuid.uuid4()),
            "customer_id": 1,
            "total_amount": 9.99,
        },
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def produce(queue_name: str, count: int) -> None:
    client = _client()
    queue_url = _queue_url(client, queue_name)

    start = time.monotonic()
    for i in range(count):
        client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(_fake_order_event()))
        if (i + 1) % 500 == 0:
            elapsed = time.monotonic() - start
            print(f"published {i + 1}/{count} ({(i + 1) / elapsed:.0f} events/sec so far)")

    elapsed = time.monotonic() - start
    print(f"done: published {count} events in {elapsed:.1f}s ({count / elapsed:.0f} events/sec)")


def consume(queue_name: str, delay_ms: float, max_batches: int | None) -> None:
    """A deliberately slow, standalone consumer for the backpressure demo --
    NOT app/worker.py. Just receives + deletes with an artificial per-message
    delay, so a single instance caps out around 1000/delay_ms messages/sec,
    letting the queue-depth growth (and later drain) actually be visible.
    """
    client = _client()
    queue_url = _queue_url(client, queue_name)

    processed = 0
    batches = 0
    start = time.monotonic()
    while max_batches is None or batches < max_batches:
        response = client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=5)
        messages = response.get("Messages", [])
        batches += 1
        for message in messages:
            time.sleep(delay_ms / 1000)
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            processed += 1
            if processed % 100 == 0:
                elapsed = time.monotonic() - start
                print(f"consumed {processed} ({processed / elapsed:.0f} events/sec so far)")

    elapsed = time.monotonic() - start
    rate = processed / elapsed if elapsed else 0
    print(f"done: consumed {processed} events in {elapsed:.1f}s ({rate:.0f} events/sec)")


def depth(queue_name: str, watch: bool) -> None:
    client = _client()
    queue_url = _queue_url(client, queue_name)

    while True:
        attrs = client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessagesVisible", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        visible = attrs["ApproximateNumberOfMessagesVisible"]
        in_flight = attrs["ApproximateNumberOfMessagesNotVisible"]
        print(f"visible={visible} in_flight={in_flight}")
        if not watch:
            return
        time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-name", default=os.environ.get("SQS_QUEUE_NAME", "order-events"))
    subparsers = parser.add_subparsers(dest="mode", required=True)

    produce_parser = subparsers.add_parser("produce", help="Publish a burst of synthetic OrderCreated events")
    produce_parser.add_argument("--count", type=int, default=5000)

    consume_parser = subparsers.add_parser("consume", help="Drain the queue at a fixed, artificially slow rate")
    consume_parser.add_argument("--delay-ms", type=float, default=2.0, help="~2ms/message caps this at ~500/sec")
    consume_parser.add_argument("--max-batches", type=int, default=None, help="Stop after N receive_message calls")

    depth_parser = subparsers.add_parser("depth", help="Print the current queue depth")
    depth_parser.add_argument("--watch", action="store_true", help="Keep printing every 2s until Ctrl+C")

    args = parser.parse_args()

    if args.mode == "produce":
        produce(args.queue_name, args.count)
    elif args.mode == "consume":
        consume(args.queue_name, args.delay_ms, args.max_batches)
    elif args.mode == "depth":
        depth(args.queue_name, args.watch)


if __name__ == "__main__":
    main()
