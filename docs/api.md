# V0 API Reference

Interactive, always-up-to-date docs are served by FastAPI at `/docs` (Swagger UI) and `/redoc`. This file is a quick static reference.

Base URL (local): `http://localhost:8000`

## Health

| Method | Path      | Description         |
| ------ | --------- | -------------------- |
| GET    | `/health` | Liveness check       |

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

## Orders

| Method | Path                     | Body                                                     | Notes                                             |
| ------ | ------------------------- | --------------------------------------------------------- | --------------------------------------------------- |
| POST   | `/orders`                 | `{customer_id, items: [{product_id, quantity}]}`           | validates stock, decrements it, charges payment    |
| GET    | `/orders`                 | query: `customer_id?`, `skip`, `limit`                      | list, optionally filtered by customer               |
| GET    | `/orders/{id}`            | -                                                           | 404 if not found                                    |
| POST   | `/orders/{id}/cancel`     | -                                                           | 409 if already `CANCELLED`/`PAYMENT_FAILED`; restocks items |

Order `status` values: `PENDING` (transient, never observed by clients) -> `PAID` or `PAYMENT_FAILED` -> (optionally) `CANCELLED`.

## Payments (fake provider)

| Method | Path        | Body                       | Notes                                             |
| ------ | ------------ | --------------------------- | --------------------------------------------------- |
| POST   | `/payments`  | `{order_id, amount}`         | Randomly returns `SUCCESS` (80%) or `FAILED` (20%) |

This endpoint stands in for a real external payment gateway. The order flow calls the same logic in-process (no network hop, since this is a monolith), but the endpoint documents the eventual external contract.
