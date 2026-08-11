"""V7 (Microservices) experiments.

Makes the curriculum's critical exercise observable:

    python loadtest/microservices_experiment.py fault-isolation
    python loadtest/microservices_experiment.py compare-latency
    python loadtest/microservices_experiment.py checklist
    python loadtest/microservices_experiment.py all

Prerequisites: `docker compose up --build -d` (gateway on :8000).

    $env:APP_URL = "http://localhost:8000"

See docs/adr/ADR-008-microservices.md.
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
import uuid

import httpx

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
COMPOSE_FILE = os.environ.get(
    "COMPOSE_FILE",
    os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml"),
)


def _log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _header(title: str, expectation: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)
    print(f"Expected: {expectation}\n", flush=True)


def _compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _seed(client: httpx.Client) -> tuple[int, int]:
    email = f"ms-{uuid.uuid4().hex[:8]}@example.com"
    customer = client.post("/customers", json={"name": "MS Buyer", "email": email})
    customer.raise_for_status()
    product = client.post(
        "/products",
        json={"name": "MS Widget", "price": 15.0, "stock_quantity": 100},
    )
    product.raise_for_status()
    return customer.json()["id"], product.json()["id"]


def fault_isolation() -> None:
    _header(
        "Fault isolation — stop Product, keep Payment healthy",
        "POST /orders fails; GET /payments health (via payment path) still works; "
        "restart Product and orders recover.",
    )
    with httpx.Client(base_url=APP_URL, timeout=10.0) as client:
        customer_id, product_id = _seed(client)

        _log("Stopping product container ...")
        stop = _compose("stop", "product")
        if stop.returncode != 0:
            _log(f"compose stop failed: {stop.stderr.strip()}")
            sys.exit(1)
        time.sleep(2)

        payment_health = client.get("/payment/health")
        _log(f"Payment liveness via gateway: {payment_health.status_code} {payment_health.text}")

        order = client.post(
            "/orders",
            json={
                "customer_id": customer_id,
                "items": [{"product_id": product_id, "quantity": 1}],
            },
        )
        _log(f"Order while Product down: {order.status_code} {order.text[:200]}")
        assert order.status_code >= 500 or order.status_code == 503, (
            "expected order create to fail when Product is down"
        )

        _log("Starting product container ...")
        _compose("start", "product")
        for _ in range(30):
            try:
                if client.get("/product/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)

        order2 = client.post(
            "/orders",
            json={
                "customer_id": customer_id,
                "items": [{"product_id": product_id, "quantity": 1}],
            },
        )
        _log(f"Order after Product recover: {order2.status_code}")
        assert order2.status_code == 201, order2.text
        _log("PASS — blast radius stayed inside the Product dependency.")


def compare_latency(samples: int = 20) -> None:
    _header(
        "Order latency under microservices (network hops)",
        "p50/p95 of POST /orders includes Product reserve + Payment charge hops. "
        "Use this number in the ADR cost/complexity discussion — not as a "
        "monolith benchmark (the monolith is gone).",
    )
    with httpx.Client(base_url=APP_URL, timeout=30.0) as client:
        customer_id, product_id = _seed(client)
        latencies: list[float] = []
        for i in range(samples):
            t0 = time.perf_counter()
            resp = client.post(
                "/orders",
                json={
                    "customer_id": customer_id,
                    "items": [{"product_id": product_id, "quantity": 1}],
                },
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code == 201:
                latencies.append(elapsed_ms)
            else:
                _log(f"sample {i}: status={resp.status_code}")
        if not latencies:
            _log("FAIL — no successful orders")
            sys.exit(1)
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]
        _log(f"samples={len(latencies)} p50={p50:.1f}ms p95={p95:.1f}ms")
        _log("PASS — record these in your ADR notes.")


def checklist() -> None:
    _header(
        "Monolith vs Microservices checklist (fill while looking at the running stack)",
        "Answer each claim with evidence from Compose/AWS, not memory.",
    )
    rows = [
        ("Cost", "Count containers/DBs/TGs vs one app+one db"),
        ("Complexity", "Trace Order → Product reserve → Payment charge in logs"),
        ("Scalability", "Note Product can scale without Payment tasks"),
        ("Deployment", "Redeploy only payment service; orders keep serving"),
        ("Team ownership", "Which repo paths/DB names each team owns"),
        ("Reliability", "Run fault-isolation — Payment stays up"),
        ("Data consistency", "Crash story between reserve and order insert"),
    ]
    for title, hint in rows:
        _log(f"[{title}] {hint}")
    _log("See docs/adr/ADR-008-microservices.md for the written comparison.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["fault-isolation", "compare-latency", "checklist", "all"],
    )
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()

    if args.command in ("fault-isolation", "all"):
        fault_isolation()
    if args.command in ("compare-latency", "all"):
        compare_latency(samples=args.samples)
    if args.command in ("checklist", "all"):
        checklist()


if __name__ == "__main__":
    main()
