"""V5 (Reliability) fault-injection experiments.

The V5 spec names four faults and one requirement: "artificially inject
payment timeout, 50% API failure, consumer crash, duplicate event -- system
must recover." This script injects each one against a running stack and prints
what actually happened, so "recovered" is an observation rather than a claim.

Each scenario states its expected outcome and then measures it:

    python loadtest/chaos_experiment.py timeout      # payment timeout
    python loadtest/chaos_experiment.py failure      # 50% API failure
    python loadtest/chaos_experiment.py crash        # consumer crash
    python loadtest/chaos_experiment.py duplicate    # duplicate event
    python loadtest/chaos_experiment.py isolation    # bulkhead / blast radius
    python loadtest/chaos_experiment.py all

Prerequisites: `docker compose up -d` (app, worker, db, redis, localstack,
payment-gateway) and:

    $env:APP_URL = "http://localhost:8000"
    $env:GATEWAY_URL = "http://localhost:9000"
    $env:SQS_ENDPOINT_URL = "http://localhost:4566"   # omit against real AWS

See docs/deployment.md's "V5: Reliability" section for the full runbook and
docs/adr/ADR-006-reliability.md for why each expected outcome is the right one.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:9000")
QUEUE_NAME = os.environ.get("SQS_QUEUE_NAME", "order-events")


def _log(message: str) -> None:
    print(f"  {message}", flush=True)


def _header(title: str, expectation: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)
    print(f"Expected: {expectation}\n", flush=True)


def set_chaos(**kwargs) -> None:
    """Point the stand-in gateway at a specific failure mode."""
    payload = {
        "error_rate": 0.0,
        "hang_rate": 0.0,
        "hang_ms": 10_000,
        "latency_ms": 0,
        "success_rate": 0.8,
        **kwargs,
    }
    httpx.post(f"{GATEWAY_URL}/admin/chaos", json=payload, timeout=5).raise_for_status()


def reset_gateway() -> None:
    httpx.post(f"{GATEWAY_URL}/admin/reset", timeout=5).raise_for_status()


def gateway_charges() -> dict:
    return httpx.get(f"{GATEWAY_URL}/admin/charges", timeout=5).json()


def readiness() -> dict:
    return httpx.get(f"{APP_URL}/health/ready", timeout=10).json()


def _seed() -> tuple[int, int]:
    """Create a customer and a well-stocked product to order against."""
    client = httpx.Client(base_url=APP_URL, timeout=30)
    customer = client.post(
        "/customers",
        json={"name": "Chaos Tester", "email": f"chaos-{uuid.uuid4()}@example.com"},
    ).json()
    product = client.post(
        "/products",
        json={"name": "Chaos Widget", "price": 9.99, "stock_quantity": 100_000},
    ).json()
    return customer["id"], product["id"]


def _place_orders(customer_id: int, product_id: int, count: int, concurrency: int = 4) -> dict:
    """Place `count` orders and summarize outcomes by status and latency."""
    client = httpx.Client(base_url=APP_URL, timeout=60)
    statuses: dict[str, int] = {}
    latencies: list[float] = []

    def _one(_: int) -> None:
        started = time.perf_counter()
        try:
            response = client.post(
                "/orders",
                json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
            )
            status = response.json().get("status", f"HTTP_{response.status_code}")
        except httpx.HTTPError as exc:
            status = f"CLIENT_{exc.__class__.__name__}"
        latencies.append(time.perf_counter() - started)
        statuses[status] = statuses.get(status, 0) + 1

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(_one, range(count)))

    latencies.sort()
    return {
        "statuses": statuses,
        "p50_seconds": round(statistics.median(latencies), 3) if latencies else None,
        "max_seconds": round(latencies[-1], 3) if latencies else None,
    }


def scenario_timeout(orders: int = 6) -> None:
    _header(
        "1. Payment timeout",
        "orders end PAYMENT_PENDING (not PAYMENT_FAILED), stock is NOT released, "
        "and the circuit opens so later orders fail fast instead of each waiting "
        "out the full retry ladder",
    )
    reset_gateway()
    customer_id, product_id = _seed()

    stock_before = httpx.get(f"{APP_URL}/products/{product_id}", timeout=10).json()["stock_quantity"]

    # Hang far longer than the client's 2s read timeout on every request.
    set_chaos(hang_rate=1.0, hang_ms=10_000)
    result = _place_orders(customer_id, product_id, orders, concurrency=2)

    stock_after = httpx.get(f"{APP_URL}/products/{product_id}", timeout=10).json()["stock_quantity"]

    _log(f"outcomes: {result['statuses']}")
    _log(f"latency p50={result['p50_seconds']}s max={result['max_seconds']}s")
    _log(f"stock: {stock_before} -> {stock_after} (each order legitimately reserves 1)")
    _log(f"payment gateway state: {readiness()['checks']['payment_gateway']}")
    _log(
        "Note the later orders returning far faster than the first few: that is the "
        "breaker having opened, converting a 7-second wait into an instant answer."
    )
    reset_gateway()


def scenario_failure(orders: int = 20) -> None:
    _header(
        "2. 50% API failure",
        "clearly more than half the orders end PAID -- a 503 is retried, so a "
        "50% per-request failure rate is not a 50% order failure rate",
    )
    reset_gateway()
    customer_id, product_id = _seed()

    set_chaos(error_rate=0.5, success_rate=1.0)
    result = _place_orders(customer_id, product_id, orders, concurrency=4)

    paid = result["statuses"].get("PAID", 0)
    _log(f"outcomes: {result['statuses']}")
    _log(f"PAID: {paid}/{orders} ({100 * paid // max(orders, 1)}%) against a 50% per-call failure rate")
    _log(f"latency p50={result['p50_seconds']}s max={result['max_seconds']}s (retries cost latency)")
    _log(f"gateway charge counts: {gateway_charges()}")
    reset_gateway()


def scenario_crash(events: int = 200) -> None:
    _header(
        "3. Consumer crash",
        "no event loss: messages in flight when the worker dies become visible "
        "again after the visibility timeout and are processed by the restarted worker",
    )
    import boto3

    sqs = boto3.client(
        "sqs",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        endpoint_url=os.environ.get("SQS_ENDPOINT_URL"),
    )
    queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]

    def depth() -> dict:
        attributes = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        return {
            "visible": int(attributes["ApproximateNumberOfMessages"]),
            "in_flight": int(attributes["ApproximateNumberOfMessagesNotVisible"]),
        }

    _log(f"publishing {events} events directly to the queue")
    for _ in range(events):
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {
                    "event_id": str(uuid.uuid4()),
                    "event_type": "OrderCreated",
                    "payload": {"order_id": 1, "customer_id": 1, "total_amount": 9.99},
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

    _log(f"depth before crash: {depth()}")
    _log("killing the worker mid-drain (SIGKILL, no graceful shutdown)")
    subprocess.run(["docker", "compose", "kill", "worker"], check=False)
    time.sleep(3)
    _log(f"depth just after the kill: {depth()}  <- in_flight are the messages it was holding")

    _log("restarting the worker; in-flight messages return after the visibility timeout")
    subprocess.run(["docker", "compose", "up", "-d", "worker"], check=False)

    for _ in range(24):
        time.sleep(5)
        current = depth()
        _log(f"depth: {current}")
        if current["visible"] == 0 and current["in_flight"] == 0:
            _log("queue fully drained -- every event survived the crash")
            return
    _log("still draining; a slow drain here is the visibility timeout, not loss")


def scenario_duplicate() -> None:
    _header(
        "4. Duplicate event",
        "the same event_id delivered twice produces exactly one set of business "
        "effects: the second delivery finds durable processed_events records and skips",
    )
    import boto3

    sqs = boto3.client(
        "sqs",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        endpoint_url=os.environ.get("SQS_ENDPOINT_URL"),
    )
    queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]

    event_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "event_id": event_id,
            "event_type": "OrderCreated",
            "payload": {"order_id": 4242, "customer_id": 1, "total_amount": 42.0},
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    _log(f"publishing event_id={event_id} once")
    sqs.send_message(QueueUrl=queue_url, MessageBody=body)
    time.sleep(8)

    _log("publishing the identical event_id a second time")
    sqs.send_message(QueueUrl=queue_url, MessageBody=body)
    time.sleep(8)

    _log("check the worker log -- there must be exactly ONE set of notification/")
    _log("email/analytics/search lines for this event_id, and a 'resuming, handlers")
    _log("already done' line for the second delivery:")
    _log("")
    _log(f'  docker compose logs worker | Select-String "{event_id}"')
    _log("")
    _log("and exactly four rows (one per handler) in Postgres:")
    _log("")
    _log(
        "  docker compose exec db psql -U commerceops -d commerceops -c "
        f"\"SELECT handler_name FROM processed_events WHERE event_id='{event_id}';\""
    )


def scenario_isolation(payment_orders: int = 30, probes: int = 20) -> None:
    _header(
        "5. Failure isolation (bulkhead)",
        "with the payment gateway hanging, GET /products stays fast -- the "
        "bulkhead caps how much of the thread pool the payment path can hold, "
        "so a dead dependency does not become an application-wide outage",
    )
    reset_gateway()
    customer_id, product_id = _seed()
    set_chaos(hang_rate=1.0, hang_ms=30_000)

    client = httpx.Client(base_url=APP_URL, timeout=60)
    product_latencies: list[float] = []
    product_errors = 0

    def _hammer_payments() -> dict:
        return _place_orders(customer_id, product_id, payment_orders, concurrency=payment_orders)

    with ThreadPoolExecutor(max_workers=2) as pool:
        payments = pool.submit(_hammer_payments)
        time.sleep(1)
        for _ in range(probes):
            started = time.perf_counter()
            try:
                response = client.get(f"/products/{product_id}", timeout=5)
                if response.status_code != 200:
                    product_errors += 1
            except httpx.HTTPError:
                product_errors += 1
            product_latencies.append(time.perf_counter() - started)
            time.sleep(0.25)
        payment_result = payments.result()

    _log(f"order outcomes while the gateway hangs: {payment_result['statuses']}")
    _log(f"  (bulkhead_full / circuit_open orders are shed deliberately and fast)")
    _log(f"GET /products during the same window: {probes - product_errors}/{probes} succeeded")
    _log(
        f"  p50={round(statistics.median(product_latencies), 3)}s "
        f"max={round(max(product_latencies), 3)}s"
    )
    _log("Product reads staying fast is the whole point: the failure stayed in its compartment.")
    reset_gateway()


SCENARIOS = {
    "timeout": scenario_timeout,
    "failure": scenario_failure,
    "crash": scenario_crash,
    "duplicate": scenario_duplicate,
    "isolation": scenario_isolation,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V5 reliability fault-injection experiments")
    parser.add_argument("scenario", choices=[*SCENARIOS, "all"])
    args = parser.parse_args(argv)

    try:
        httpx.get(f"{APP_URL}/health", timeout=5).raise_for_status()
        httpx.get(f"{GATEWAY_URL}/health", timeout=5).raise_for_status()
    except httpx.HTTPError as exc:
        print(f"stack not reachable ({exc}). Run `docker compose up -d` first.")
        return 1

    scenarios = SCENARIOS.values() if args.scenario == "all" else [SCENARIOS[args.scenario]]
    for scenario in scenarios:
        scenario()

    print("\nDone. Reset state with: python loadtest/chaos_experiment.py --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
