"""V2 (Horizontal Scaling) load test.

Simulates the flash-sale traffic shape from the learning project spec:
mostly product listing/browsing, with a smaller slice of real order
creation. Point this at either the local Docker Compose stack or the
deployed ALB DNS name -- see docs/deployment.md's "V2: Load testing"
section for the staged 100/500/1000/5000 req/s run commands.

    locust -f loadtest/locustfile.py --host http://localhost:8000
    locust -f loadtest/locustfile.py --host http://<alb_dns_name> \\
        --headless -u 500 -r 50 --run-time 10m

This file intentionally targets a scratch/test environment: test_start
seeds its own customers and products rather than assuming any exist.
"""

import random

import requests
from locust import HttpUser, between, events, task

PRODUCT_IDS: list[int] = []
CUSTOMER_IDS: list[int] = []

SEED_PRODUCT_COUNT = 20
SEED_CUSTOMER_COUNT = 20
# High stock so a 10-minute flash-sale run doesn't legitimately sell out and
# start masking scaling behavior behind 409 "out of stock" responses.
SEED_PRODUCT_STOCK = 1_000_000


@events.test_start.add_listener
def seed_data(environment, **kwargs):
    """Create a small pool of products/customers once, before any users spawn.

    Runs outside any HttpUser, so it uses `requests` directly against
    `environment.host` rather than a locust client.
    """
    host = environment.host
    if not host:
        raise ValueError("Pass --host, e.g. http://localhost:8000 or the ALB DNS name")

    for i in range(SEED_PRODUCT_COUNT):
        resp = requests.post(
            f"{host}/products",
            json={
                "name": f"loadtest-product-{i}",
                "price": 9.99,
                "stock_quantity": SEED_PRODUCT_STOCK,
            },
            timeout=10,
        )
        resp.raise_for_status()
        PRODUCT_IDS.append(resp.json()["id"])

    for i in range(SEED_CUSTOMER_COUNT):
        resp = requests.post(
            f"{host}/customers",
            json={"name": f"Loadtest Customer {i}", "email": f"loadtest{i}@example.com"},
            timeout=10,
        )
        resp.raise_for_status()
        CUSTOMER_IDS.append(resp.json()["id"])


class CommerceOpsUser(HttpUser):
    # A real shopper pauses between actions; tune down for a more aggressive
    # request rate, or drive rate via -u/-r instead of shrinking this.
    wait_time = between(1, 3)

    @task(10)
    def list_products(self):
        # ~90%+ of flash-sale traffic per the V3 problem statement — kept
        # dominant here too so V2's scaling experiment reflects it.
        self.client.get("/products", name="/products [list]")

    @task(3)
    def get_product(self):
        if not PRODUCT_IDS:
            return
        product_id = random.choice(PRODUCT_IDS)
        self.client.get(f"/products/{product_id}", name="/products/[id]")

    @task(1)
    def create_order(self):
        if not PRODUCT_IDS or not CUSTOMER_IDS:
            return
        items = [
            {"product_id": random.choice(PRODUCT_IDS), "quantity": random.randint(1, 3)}
            for _ in range(random.randint(1, 2))
        ]
        payload = {"customer_id": random.choice(CUSTOMER_IDS), "items": items}
        self.client.post("/orders", json=payload, name="/orders [create]")

    @task(1)
    def health_check(self):
        self.client.get("/health")
