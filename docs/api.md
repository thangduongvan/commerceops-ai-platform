# V0 API Reference

Interactive, always-up-to-date docs are served by FastAPI at `/docs` (Swagger UI) and `/redoc`. This file is a quick static reference.

Base URL (local): `http://localhost:8000`

## Health

| Method | Path             | Description                                                    |
| ------ | ---------------- | -------------------------------------------------------------- |
| GET    | `/health`        | Liveness. Shallow by design — touches no dependency            |
| GET    | `/health/ready`  | V5/V6: deep dependency probe. Always HTTP 200; see `status` in the body |

**Why two (V5)**: `/health` is what the ALB target group polls, so it deliberately checks nothing but "is this process serving HTTP?". A deep check here would mark *every* task unhealthy the moment a shared dependency failed — emptying the target group, returning 503 to everything, and making ECS replace tasks that were working fine. The health check would cause a worse outage than the fault.

`/health/ready` reports database, Redis, queue, payment gateway state (including circuit-breaker state and bulkhead usage), and (V6) read-replica lag for humans and dashboards. It returns **HTTP 200 even when degraded**, with `"status": "ok" | "degraded"` in the body, so nothing automated acts on it:

```json
{
  "status": "degraded",
  "environment": "local",
  "checks": {
    "database": { "ok": true },
    "redis": { "ok": true, "required": false },
    "queue": { "ok": true, "required": false },
    "payment_gateway": { "reachable": true, "circuit_state": "OPEN", "bulkhead_in_use": 3 },
    "database_replica": {
      "ok": true,
      "required": false,
      "lag_seconds": 0.12,
      "max_lag_seconds": 5.0,
      "lagging": false
    }
  }
}
```

`required: false` on Redis, the queue, and the replica records that none of them is needed to serve traffic correctly — cache reads fall through to Postgres (V3), a failed publish costs an order its async side effects, not the order (V4/V5), and product reads fall open to the primary when the replica is down (V6). See [ADR-006](adr/ADR-006-reliability.md) and [ADR-007](adr/ADR-007-database-ha.md).

When no replica is configured, `database_replica` reports `"enabled": false` with `lag_seconds: null` and still `ok: true` — a missing replica must not mark the process degraded.

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

**Read replica (V6)**: both GET endpoints also use the asynchronous read replica when `READ_REPLICA_ENABLED=true` (Compose `db-replica`, or the RDS replica in AWS). They may therefore be stale by up to the current replica lag in addition to the Redis TTL. On replica failure they fall open to the primary and log `read_replica_unavailable`. `POST` / `PUT` stay on the primary. Order and customer endpoints never use the replica — a customer reading their own order immediately after placing it cannot tolerate lag. See [ADR-007](adr/ADR-007-database-ha.md).

## Orders

| Method | Path                     | Body                                                     | Notes                                             |
| ------ | ------------------------- | --------------------------------------------------------- | --------------------------------------------------- |
| POST   | `/orders`                 | `{customer_id, items: [{product_id, quantity}]}`           | validates stock, decrements it, charges payment    |
| GET    | `/orders`                 | query: `customer_id?`, `skip`, `limit`                      | list, optionally filtered by customer               |
| GET    | `/orders/{id}`            | -                                                           | 404 if not found                                    |
| POST   | `/orders/{id}/cancel`     | -                                                           | 409 if already `CANCELLED`/`PAYMENT_FAILED`/`PAYMENT_PENDING`; restocks items |

Order `status` values: `PENDING` (transient, never observed by clients) -> `PAID`, `PAYMENT_FAILED`, or `PAYMENT_PENDING` -> (optionally) `CANCELLED`.

**`PAYMENT_PENDING` (V5)**: the payment gateway never answered — a timeout, an exhausted retry budget, an open circuit breaker, or a request shed by the bulkhead. It does **not** mean the payment failed; it means the outcome is genuinely unknown and the card may well have been charged.

Two consequences a client needs to know about:

* **Stock is not released**, unlike `PAYMENT_FAILED`. Releasing inventory for an order that may have been paid for is the worse error, so the reservation is held until the order is reconciled against the gateway (V12, Saga).
* **The order cannot be cancelled** (409), because cancellation restocks. It has to be resolved into `PAID` or `PAYMENT_FAILED` first.

**Asynchronous processing (V4)**: `POST /orders` and `POST /orders/{id}/cancel` no longer wait on notification, analytics, email, or search-indexing side effects — those are published as events to an SQS queue and handled off the request path by a separate worker process (see [ADR-005](adr/ADR-005-async-processing.md)). A response is not a guarantee those side effects have run yet, only that the order itself was persisted.

## Payments

| Method | Path        | Body                       | Notes                                                    |
| ------ | ------------ | --------------------------- | -------------------------------------------------------- |
| POST   | `/payments`  | `{order_id, amount}`         | Returns `SUCCESS`, `FAILED`, or `UNKNOWN`, plus a `reason` |

**V5**: this delegates to a real HTTP call against the payment gateway ([app/payment/gateway_client.py](../app/payment/gateway_client.py)) — up to four attempts with jittered backoff, behind a circuit breaker and a bulkhead, carrying a stable `Idempotency-Key: order-{id}` so a retried charge is never a second charge. Through V4 it was an in-process coin flip.

| `status`  | Meaning                                     | `reason` examples                                            |
| --------- | ------------------------------------------- | ------------------------------------------------------------ |
| `SUCCESS` | Charged                                      | `approved`                                                    |
| `FAILED`  | The gateway answered and declined            | `declined`, `http_400`                                        |
| `UNKNOWN` | No answer — the charge may have happened     | `timeout`, `retries_exhausted`, `circuit_open`, `bulkhead_full` |

The `reason` field matters operationally: `declined` is a customer problem, `circuit_open` is an outage, and before V5 the two were indistinguishable from the caller's side. `UNKNOWN` is what produces an order in `PAYMENT_PENDING`.

In local Docker Compose the gateway is a separate service on port 9000 ([fake_gateway/](../fake_gateway/main.py)); in AWS it's an ECS sidecar on `localhost:9000`. A real integration would point `PAYMENT_GATEWAY_URL` at the provider and delete the stand-in.
