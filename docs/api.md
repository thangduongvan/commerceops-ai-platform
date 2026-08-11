# V0 API Reference

Interactive, always-up-to-date docs are served by FastAPI at `/docs` (Swagger UI) and `/redoc`. This file is a quick static reference.

Base URL (local gateway): `http://localhost:8000` — nginx routes to Product / Order / Payment (V7). Per-service Swagger: `:8001` / `:8002` / `:8003`.

## Health

| Method | Path             | Description                                                    |
| ------ | ---------------- | -------------------------------------------------------------- |
| GET    | `/health`        | Liveness. Shallow by design — touches no dependency            |
| GET    | `/health/ready`  | Deep probe for *this* service. Always HTTP 200; see `status`   |

**V7**: each service reports only its own dependencies. Product: DB + Redis + replica. Order: DB + Redis + SQS + `product_service` / `payment_service` probes. Payment: DB + payment gateway (circuit state). Gateway `/health` returns a static ok for local smoke checks; ALB uses each target group's `/health`.

**Why two (V5)**: `/health` is what the ALB target group polls, so it deliberately checks nothing but "is this process serving HTTP?". A deep check here would mark *every* task unhealthy the moment a shared dependency failed.

`required: false` on Redis, the queue, peer services, and the replica records that none of them must take the process out of the load balancer. See [ADR-006](adr/ADR-006-reliability.md), [ADR-007](adr/ADR-007-database-ha.md), [ADR-008](adr/ADR-008-microservices.md).

## Customers

| Method | Path                | Body                          | Notes                              |
| ------ | -------------------- | ------------------------------ | ----------------------------------- |
| POST   | `/customers`         | `{name, email}`                | 409 if email already registered     |
| GET    | `/customers/{id}`    | -                               | 404 if not found                    |

## Products

| Method | Path               | Body                                                     | Notes                     |
| ------ | ------------------- | --------------------------------------------------------- | -------------------------- |
| POST   | `/products`         | `{name, description?, price, stock_quantity?}`            |                             |
| GET    | `/products`         | query: `skip`, `limit`                                     | list, cache-aside (see below) |
| GET    | `/products/{id}`    | -                                                           | 404 if not found, cache-aside (see below) |
| PUT    | `/products/{id}`    | any subset of `{name, description, price, stock_quantity}` | partial update, invalidates the detail cache |

**Caching (V3)**: both GET endpoints are served cache-aside from Redis (`cache_ttl_seconds`, default 15s). `GET /products/{id}` is invalidated immediately on `PUT`; `GET /products` listings may lag a write by up to `cache_ttl_seconds` (bounded staleness, not actively invalidated — see [ADR-004](adr/ADR-004-caching.md)). If Redis is unavailable, both endpoints keep working by falling back to PostgreSQL.

**Read replica (V6)**: both GET endpoints also use the asynchronous read replica when `READ_REPLICA_ENABLED=true` (Compose `product-db-replica`, or the RDS replica in AWS). They may therefore be stale by up to the current replica lag in addition to the Redis TTL. On replica failure they fall open to the primary and log `read_replica_unavailable`. `POST` / `PUT` stay on the primary. See [ADR-007](adr/ADR-007-database-ha.md).

### Internal stock (V7 — Order → Product)

| Method | Path                         | Body                                              | Notes                          |
| ------ | ----------------------------- | -------------------------------------------------- | ------------------------------ |
| POST   | `/internal/stock/reserve`     | `{items: [{product_id, quantity}]}`                | Atomic check+decrement; returns `unit_price`s |
| POST   | `/internal/stock/release`     | `{items: [{product_id, quantity}]}`                | Compensating restock (204)     |

Not intended for public clients; reachable via the gateway for debugging. Order uses these over HTTP instead of mutating Product rows in a shared DB.

## Orders

| Method | Path                     | Body                                                     | Notes                                             |
| ------ | ------------------------- | --------------------------------------------------------- | --------------------------------------------------- |
| POST   | `/orders`                 | `{customer_id, items: [{product_id, quantity}]}`           | V7: Product.reserve → Order DB insert → Payment.charge |
| GET    | `/orders`                 | query: `customer_id?`, `skip`, `limit`                      | list, optionally filtered by customer               |
| GET    | `/orders/{id}`            | -                                                           | 404 if not found                                    |
| POST   | `/orders/{id}/cancel`     | -                                                           | 409 if already `CANCELLED`/`PAYMENT_FAILED`/`PAYMENT_PENDING`; releases stock via Product |

Order `status` values: `PENDING` (transient, never observed by clients) -> `PAID`, `PAYMENT_FAILED`, or `PAYMENT_PENDING` -> (optionally) `CANCELLED`.

**V7 consistency**: create_order is no longer one ACID transaction across stock + order + payment. A crash between reserve and insert can leave reserved stock — reconciliation is V12. See [ADR-008](adr/ADR-008-microservices.md).

**`PAYMENT_PENDING` (V5)**: the payment gateway never answered — a timeout, an exhausted retry budget, an open circuit breaker, or a request shed by the bulkhead. It does **not** mean the payment failed; it means the outcome is genuinely unknown and the card may well have been charged.

Two consequences a client needs to know about:

* **Stock is not released**, unlike `PAYMENT_FAILED`. Releasing inventory for an order that may have been paid for is the worse error, so the reservation is held until the order is reconciled against the gateway (V12, Saga).
* **The order cannot be cancelled** (409), because cancellation restocks. It has to be resolved into `PAID` or `PAYMENT_FAILED` first.

**Asynchronous processing (V4)**: `POST /orders` and `POST /orders/{id}/cancel` no longer wait on notification, analytics, email, or search-indexing side effects — those are published as events to an SQS queue and handled off the request path by a separate worker process (see [ADR-005](adr/ADR-005-async-processing.md)). A response is not a guarantee those side effects have run yet, only that the order itself was persisted.

## Payments

| Method | Path        | Body                       | Notes                                                    |
| ------ | ------------ | --------------------------- | -------------------------------------------------------- |
| POST   | `/payments`  | `{order_id, amount}`         | Returns `SUCCESS`, `FAILED`, or `UNKNOWN`, plus a `reason` |

**V7**: Payment owns a durable `payments` row keyed by `order_id` (idempotent retries). Order calls this endpoint over HTTP (`PAYMENT_SERVICE_URL`).

**V5**: the Payment service still delegates to a real HTTP call against the payment gateway ([app/payment/gateway_client.py](../app/payment/gateway_client.py)) — up to four attempts with jittered backoff, behind a circuit breaker and a bulkhead, carrying a stable `Idempotency-Key: order-{id}` so a retried charge is never a second charge.

| `status`  | Meaning                                     | `reason` examples                                            |
| --------- | ------------------------------------------- | ------------------------------------------------------------ |
| `SUCCESS` | Charged                                      | `approved`                                                    |
| `FAILED`  | The gateway answered and declined            | `declined`, `http_400`                                        |
| `UNKNOWN` | No answer — the charge may have happened     | `timeout`, `retries_exhausted`, `circuit_open`, `bulkhead_full` |

The `reason` field matters operationally: `declined` is a customer problem, `circuit_open` is an outage, and before V5 the two were indistinguishable from the caller's side. `UNKNOWN` is what produces an order in `PAYMENT_PENDING`.

In local Docker Compose the gateway is a separate service on port 9000 ([fake_gateway/](../fake_gateway/main.py)); in AWS it's an ECS sidecar on the **Payment** task (`localhost:9000`). A real integration would point `PAYMENT_GATEWAY_URL` at the provider and delete the stand-in.
