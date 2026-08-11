# ADR-008: Microservices Split (Product / Order / Payment)

## Context

Through V6 the platform is a modular monolith: one FastAPI process, one shared Postgres (plus a product read replica), one deployable image. That matched one team and one scaling story.

The organization now has **separate teams** owning Product, Order, and Payment, and the workloads diverge:

| Domain  | Shape                         |
| ------- | ----------------------------- |
| Product | READ >>> WRITE (catalogue)    |
| Order   | WRITE + orchestration         |
| Payment | High reliability, ext. gateway|

V0's package boundaries were designed for this split ([ADR-001](ADR-001-modular-monolith.md)). V7 pays the distributed-systems cost that ADR-001 deliberately deferred.

## Decision

Split into **three deployable services** with **database-per-service**:

```text
                    API Gateway / nginx / ALB
                          │
             ┌────────────┼────────────┐
             ↓            ↓            ↓
         Product       Order        Payment
         Service       Service       Service
             │            │            │
             ↓            ↓            ↓
           DB           DB            DB
```

* **Product** — catalogue CRUD, Redis cache, V6 read replica, internal `POST /internal/stock/reserve|release`.
* **Order** — customers + orders; owns SQS publish + worker; calls Product and Payment over **sync HTTP**.
* **Payment** — durable `payments` table (idempotent on `order_id`) + gateway client/sidecar.

**Edge routing**: path-based (`/products`, `/customers`+`/orders`, `/payments`).  
**Discovery**: Compose DNS locally; **ECS Service Connect** in AWS (`product` / `payment` hostnames).  
**AWS databases**: three *logical* DBs on one RDS instance (`commerceops_product|order|payment`), created at task startup via `ensure_database`. Instance-per-service is the production isolation pattern; one instance keeps learning-project cost sane.

## Alternatives considered

* **Stay a modular monolith** — rejected against the new team/workload requirement. Still the right answer when those drivers are absent (see comparison below).
* **Split processes, keep one shared DB** — rejected. Shared tables recreate the monolith's coupling (and block independent schema evolution). Database-per-service is the point of the exercise.
* **Event-only coordination (no sync HTTP)** — deferred to V8+. Order still needs a synchronous answer for stock and payment on the request path; events fan out side effects that already leave via SQS.
* **Saga / outbox now** — deferred to V11–V12. V7 makes the lost ACID boundary *visible* (reserve → order insert → charge → compensate) without claiming a full orchestration framework.
* **Three RDS instances** — rejected for cost on a learning stack; documented as the stronger fault-isolation choice in production.

## Modular Monolith vs Microservices

| Dimension        | Modular monolith (V0–V6)                                      | Microservices (V7)                                              |
| ---------------- | ------------------------------------------------------------- | --------------------------------------------------------------- |
| Cost             | One ECS service, one DB                                       | 3+ services, 3 DBs (or 3 logical DBs), gateway, discovery       |
| Complexity       | In-process calls, one deploy                                  | Network failures, contracts, multi-deploy, distributed data     |
| Scalability      | Scale the whole app                                           | Scale Product reads independently of Payment                    |
| Deployment       | One pipeline                                                  | Independent deploys per team (same image here for simplicity)   |
| Team ownership   | Implicit module ownership                                     | Explicit service + DB ownership                                 |
| Reliability      | Process-wide blast radius; V5 mitigates deps                  | Fault isolation between services; new failure modes (partial)   |
| Data consistency | Single ACID transaction for order+stock+payment status        | Eventual / compensating; no cross-DB FK                         |

## Trade-offs

* **Lost single-transaction create_order.** Stock lives in Product DB; order rows in Order DB; payment rows in Payment DB. A crash between reserve and order insert can leave reserved stock — reconciliation is V12's job. Compensating `release` on definitive payment failure is the small Saga preview already present in V5.
* **Latency.** Every order pays two network hops (Product + Payment) on top of the gateway call Payment still makes.
* **Operational surface.** Three target groups, Service Connect namespace, three readiness shapes, more alarms.
* **AWS DB cost compromise.** Logical DBs share fate with the RDS instance (Multi-AZ still helps); they do *not* isolate storage failure the way separate instances would.

## Consequences

* Order must not import `app.product.models` or call `app.payment.service` in-process — enforced by tests and by separate processes.
* Public clients keep `http://localhost:8000` via nginx (Compose) / ALB path rules (AWS).
* Notification / Analytics / Search stay worker-side consumers of Order's SQS events (not their own services yet).
* V8+ can replace or complement sync calls with broader event-driven fan-out without undoing this split.

## Related

* [ADR-001](ADR-001-modular-monolith.md) — why we started monolith
* [ADR-006](ADR-006-reliability.md) — deferred Service Connect to V7
* [ADR-007](ADR-007-database-ha.md) — replica stays with Product
* Experiment: `python loadtest/microservices_experiment.py fault-isolation`
