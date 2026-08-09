# ADR-001: Modular Monolith for V0

## Context

CommerceOps AI Platform needs a starting point that lets a single developer (or small team) build and evolve a system that will eventually grow into microservices, event-driven architecture, CQRS, and an AI agent layer. At V0, none of that complexity is justified: there is one small team, no production traffic, and no proven need to scale any domain independently of the others.

## Decision

Build V0 as a **modular monolith**: a single deployable FastAPI process, internally organized into domain packages (`customer`, `product`, `order`, `payment`, `notification`), each with its own `models.py` / `schemas.py` / `service.py` / `router.py`. All modules share one PostgreSQL database.

## Alternatives considered

* **Microservices from day one** — one service per domain, own database, network calls between them. Rejected: introduces distributed-systems complexity (network failures, service discovery, distributed transactions) before there's any requirement (independent scaling, independent teams, independent deployment) that justifies it.
* **Unstructured single-file/script monolith** — fastest to write, but domain boundaries would blur immediately, making a later split into services much harder, and defeating the purpose of practicing "domain boundaries" as an SA concept.

## Why

* Domain packages enforce the same boundaries a future microservice split would use, without paying for network calls, service discovery, or per-service infrastructure yet.
* A single database lets order creation, stock decrement, and payment status changes commit together as one ACID transaction — a property that becomes much harder to guarantee once "product" and "order" own separate databases (this tension is deliberately revisited at V7 and resolved with the outbox/saga patterns at V11–V12).
* Fastest path to a working, testable system; every later version (V1 AWS deployment, V2 scaling, ...) builds on this same codebase rather than replacing it outright.

## Trade-offs

* All modules scale together — an order-processing spike scales the whole app, not just the order module. This limitation is exactly what motivates V7 (Microservices).
* A bug or crash in one module can affect the whole process (no fault isolation between modules yet). Addressed later with retries/circuit breakers (V5) and eventually service-level isolation (V7).
* No database-per-service — the order and product modules share one schema, so their coupling is implicit "same-database" coupling rather than an explicit API contract.

## Related decisions

* Fake in-process payment provider instead of a real gateway (keeps the focus on the order/payment interaction pattern, not third-party API integration).
* `Base.metadata.create_all()` instead of Alembic migrations — acceptable for a schema with no production data yet; revisit once schema changes need to preserve existing data.
