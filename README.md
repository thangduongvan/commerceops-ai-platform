# CommerceOps AI Platform

The [CommerceOps AI Platform learning project](../Solution%20Architect%20Learning%20Project.md): a FastAPI + PostgreSQL modular monolith covering the Customer, Product, Order, Payment, and Notification domains, evolving version by version toward microservices, event-driven architecture, CQRS, Saga, and an AI Operations Agent.

* **V0 — Local Modular Monolith**: Docker Compose, no AWS. See below.
* **V1 — AWS Foundation**: deploy the same app to ECS/Fargate behind an ALB, backed by RDS. See [Deploying to AWS (V1)](#deploying-to-aws-v1) below.
* **V2 — Horizontal Scaling**: ECS Service Auto Scaling (CPU/memory/request-count target tracking) + tuned DB connection pooling + a Locust load test, for a flash-sale traffic spike. See [Horizontal Scaling (V2)](#horizontal-scaling-v2) below.
* **V3 — Caching**: Redis cache-aside in front of the read-heavy Product endpoints (ElastiCache in AWS), so a flash sale's 90%+ read traffic mostly stops hitting Postgres at all. See [Caching (V3)](#caching-v3) below.
* **V4 — Asynchronous Processing**: order creation publishes events to an SQS queue instead of calling Notification/Analytics/Email/Search indexing in-process; a separate worker service consumes them off the request path. See [Asynchronous Processing (V4)](#asynchronous-processing-v4) below.

## V0: Local architecture

```mermaid
flowchart TD
    Client[Client] --> API[FastAPI app]
    API --> Customer[customer module]
    API --> Product[product module]
    API --> Order[order module]
    API --> Payment[payment module]
    Order --> Payment
    Order --> Notification[notification module]
    Customer --> DB[(PostgreSQL)]
    Product --> DB
    Order --> DB
```

One deployable process, one database. Each domain is a self-contained package (`models.py` / `schemas.py` / `service.py` / `router.py`) so the boundaries a future microservice split would use already exist — see [ADR-001](docs/adr/ADR-001-modular-monolith.md).

## Project structure

```
app/
├── main.py                 # FastAPI app, routers, startup table creation
├── worker.py                # V4: order-events consumer process (python -m app.worker)
├── core/
│   ├── config.py           # environment-driven settings
│   ├── database.py         # SQLAlchemy engine/session, Base, get_db
│   ├── cache.py            # V3: Redis cache-aside helpers; V4: idempotency guard
│   └── queue.py             # V4: SQS producer (publish_event)
├── customer/                # create, get
├── product/                  # create, get, list, update
├── order/                     # create, get, list, cancel (+ transaction logic)
├── payment/                    # fake payment provider
├── notification/                # V0: logs-only notification channel; V4: + email
├── analytics/                     # V4: logs-only analytics sink
└── search/                         # V4: logs-only search-indexing sink

tests/
├── unit/          # pure logic, no DB (payment randomness, notification/analytics/search logging, line-total calc, queue/worker via moto)
└── integration/   # FastAPI TestClient against an in-memory SQLite DB

docs/
├── api.md                          # endpoint reference
├── database_schema.md              # ER diagram
├── deployment.md                   # V1: step-by-step AWS deployment; V2: load-test run guide; V3: caching experiment; V4: async processing + backpressure experiment
└── adr/
    ├── ADR-001-modular-monolith.md
    ├── ADR-002-aws-foundation.md
    ├── ADR-003-horizontal-scaling.md
    ├── ADR-004-caching.md
    └── ADR-005-async-processing.md

infra/                             # V1/V2/V3/V4: Terraform (see "Deploying to AWS" below)
├── bootstrap/                      # one-time: remote state S3 bucket + DynamoDB lock table
├── modules/                        # vpc, security_groups, ecr, s3, iam, rds, elasticache, sqs, alb, ecs, autoscaling, cloudwatch
├── environments/dev/               # wires the modules together for the dev environment
└── localstack/                     # V4: LocalStack init script (creates the local order-events queue + DLQ)

loadtest/                          # V2: Locust load test; V4: SQS backpressure experiment
├── locustfile.py
├── queue_experiment.py             # V4: produce/consume/depth against SQS or LocalStack
└── requirements.txt

.github/workflows/deploy.yml        # V1: build/push to ECR + redeploy ECS on push to main
```

## Tech stack

Python 3.11+, FastAPI, SQLAlchemy 2.0, PostgreSQL 16, Redis 7 (V3 caching), boto3 + Amazon SQS / LocalStack (V4 async processing), Docker / Docker Compose, pytest + moto (V4 SQS mocking), Locust (V2 load testing).

## Running with Docker Compose (recommended)

```bash
docker compose up --build
```

* App: http://localhost:8000 (Swagger UI at `/docs`, health check at `/health`)
* Postgres: `localhost:5432` (user/password/db: `commerceops`)

## Running locally without Docker

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux

pip install -r requirements.txt

# Start Postgres only, then point the app at it:
docker compose up -d db
copy .env.example .env            # Windows; edit DATABASE_URL to use "localhost" instead of "db"
# cp .env.example .env            # macOS/Linux

uvicorn app.main:app --reload
```

To also run the V4 worker (needs `docker compose up -d localstack redis` at minimum): `python -m app.worker` in a second terminal, with the same `.env` (or `SQS_ENDPOINT_URL=http://localhost:4566`, since it's not running inside the Compose network).

## Running tests

```bash
pip install -r requirements.txt
pytest -v
```

Integration tests run against an in-memory SQLite database (no Docker/Postgres required), so they're fast and portable. Docker Compose's Postgres remains the real deployment target — this is a deliberate trade-off, documented in [docs/database_schema.md](docs/database_schema.md).

## API overview

See [docs/api.md](docs/api.md) for the full endpoint reference, or browse `/docs` once the app is running.

Core flow: create a customer -> create products -> `POST /orders` (validates stock, decrements it, calls the fake payment provider, updates order status) -> `POST /orders/{id}/cancel` (restocks items).

## Database schema

See [docs/database_schema.md](docs/database_schema.md) for the ER diagram and table notes.

## SA concepts covered in V0

* **Domain boundaries / modular monolith** — one process, clearly separated domain packages.
* **Separation of concerns** — model / schema / service / router layering within each module.
* **ACID transactions** — order creation, item persistence, and stock decrement commit atomically; payment failure triggers a compensating restock (a small preview of the Saga pattern from V12).
* **Basic API design** — resource-oriented REST endpoints, explicit status codes (404/409), request/response schemas separate from persistence models.

## Deploying to AWS (V1)

Same application code and Docker image as V0 — no microservices yet. Deployed on ECS/Fargate behind an ALB, with RDS PostgreSQL replacing the Docker Compose Postgres container.

```mermaid
flowchart TB
    Internet((Internet)) --> ALB[ALB :80]
    subgraph vpc [VPC 10.0.0.0/16]
        subgraph public [Public subnets]
            ALB
            NAT[NAT Gateway]
        end
        subgraph private [Private subnets]
            ECS["ECS Fargate task(s)<br/>commerceops-app"]
            RDS[("RDS PostgreSQL")]
        end
    end
    ALB --> ECS
    ECS --> RDS
    ECS -.pulls image.-> ECR[(ECR)]
    ECS -.logs.-> CW[CloudWatch]
    ECS -.reads secret.-> SM[Secrets Manager]
    GHA[GitHub Actions] -.OIDC.-> ECR
    GHA -.force redeploy.-> ECS
```

* **Infrastructure as code**: Terraform, under [infra/](infra/) — `bootstrap/` (one-time remote state backend), `modules/` (vpc, security_groups, ecr, s3, iam, rds, alb, ecs, autoscaling, cloudwatch), `environments/dev/` (wires them together).
* **CI/CD**: [.github/workflows/deploy.yml](.github/workflows/deploy.yml) builds the Docker image, pushes to ECR, and force-redeploys the ECS service on every push to `main` that touches `app/**`/`Dockerfile` — authenticated via GitHub OIDC (no long-lived AWS keys).
* **Full step-by-step commands** (install CLIs, bootstrap state, `terraform apply`, first image push, verification, teardown): [docs/deployment.md](docs/deployment.md).
* **Why ECS/Fargate/RDS/private-subnets, what the ALB does, what happens when a task dies, and the trade-offs made** (single NAT GW, single-AZ RDS, HTTP-only ALB, `:latest`-tag deploys): [ADR-002](docs/adr/ADR-002-aws-foundation.md).

## Horizontal Scaling (V2)

New requirement: a flash sale spikes traffic from ~300 req/s to ~5,000 req/s for ~10 minutes, then back to normal. The app must scale out and back in automatically instead of running a fixed task count sized for peak (wasteful) or for average (falls over during the spike).

```mermaid
flowchart TB
    Internet((Internet)) --> ALB
    subgraph asg [ECS Service, 2-8 tasks]
        T1[Task]
        T2[Task]
        T3["Task ..."]
    end
    ALB --> T1
    ALB --> T2
    ALB --> T3
    T1 --> RDS[("RDS PostgreSQL<br/>single instance")]
    T2 --> RDS
    T3 --> RDS
    AAS[Application Auto Scaling] -.CPU / Memory / ReqCount.-> asg
```

* **Auto Scaling**: [infra/modules/autoscaling](infra/modules/autoscaling) — three target-tracking policies (CPU 60%, memory 70%, ALB requests/target 300) on one scalable target, `min_capacity=2`/`max_capacity=8`. `desired_count` on the ECS service is now just the *initial* count; Terraform ignores drift on it so Auto Scaling can own it at runtime.
* **DB connection pool**: [app/core/database.py](app/core/database.py) / [app/core/config.py](app/core/config.py) — explicit `pool_size`/`max_overflow` per task, sized against RDS's connection ceiling at `max_capacity` tasks.
* **Load test**: [loadtest/locustfile.py](loadtest/locustfile.py) (Locust) — run the staged 100/500/1,000/5,000 req/s experiment from [docs/deployment.md](docs/deployment.md#6-v2-load-testing-horizontal-scaling).
* **Why app-tier scaling alone doesn't solve DB scaling** (connections, CPU/IOPS, hot-row contention — and why that motivates V3's cache rather than a bigger DB immediately): [ADR-003](docs/adr/ADR-003-horizontal-scaling.md).

## Caching (V3)

New requirement: during a flash sale, product listing/browsing is 90%+ of traffic, and product data changes relatively infrequently. Rather than scaling Postgres itself, a Redis cache-aside layer absorbs most of that read traffic before it ever reaches the database.

```mermaid
flowchart TB
    Internet((Internet)) --> ALB
    subgraph asg [ECS Service, 2-8 tasks]
        T1[Task]
        T2["Task ..."]
    end
    ALB --> T1
    ALB --> T2
    T1 -->|1: check cache| Redis[("ElastiCache Redis<br/>single node")]
    T2 --> Redis
    T1 -->|"2: miss -> query"| RDS[("RDS PostgreSQL")]
    T2 --> RDS
    RDS -->|"3: populate, TTL+jitter"| Redis
```

* **Cache-aside on the Product endpoints**: [app/product/service.py](app/product/service.py), using the small helper module [app/core/cache.py](app/core/cache.py) — `GET /products/{id}` (key `product:{id}`, invalidated on `PUT`) and `GET /products` (key `products:list:{skip}:{limit}`, TTL-only — see ADR-004 for why listings aren't actively invalidated).
* **Graceful degradation**: every Redis call is wrapped so a connection error/timeout degrades to a cache miss, never an application error — Redis is never the source of truth, Postgres is. A `CACHE_ENABLED` env var can also disable caching entirely for the "without cache vs with cache" experiment.
* **Infra**: [infra/modules/elasticache](infra/modules/elasticache) — single-node ElastiCache Redis cluster, private-subnet-only via a new `redis` security group (ECS -> Redis, same three-tier pattern as ALB -> ECS -> RDS).
* **Load test**: [loadtest/locustfile.py](loadtest/locustfile.py) now biases `get_product` toward a small "hot" subset of products, so the cache hit-ratio experiment in [docs/deployment.md](docs/deployment.md#7-v3-caching) has a realistic traffic shape to measure against.
* **Why Redis over a per-process cache or a read replica, and explicit answers to "what happens when Redis dies / should it be the source of truth / how do you invalidate it"**: [ADR-004](docs/adr/ADR-004-caching.md).

## Asynchronous Processing (V4)

New requirement: order creation also needs to trigger Analytics, Email, and Search indexing, alongside the existing Notification — none of which the customer placing the order needs to wait on. Chaining all four onto the request path (the spec's explicit "bad design") makes every order slower and lets a slow/flaky side effect fail an otherwise-successful order. V4's fix: publish one small event per order instead, and let a separate worker process fan out to all four side effects off the request path.

```mermaid
flowchart LR
    Client --> API[FastAPI app]
    API -->|"1: commit"| DB[(PostgreSQL)]
    API -->|"2: publish_event"| SQS[["SQS order-events<br/>+ DLQ"]]
    SQS -->|"3: long-poll"| Worker["worker service<br/>(scales on queue depth)"]
    Worker --> Notification
    Worker --> Analytics
    Worker --> Email
    Worker --> Search[Search indexing]
```

* **Producer**: [app/order/service.py](app/order/service.py) publishes `OrderCreated`/`OrderPaid`/`OrderPaymentFailed`/`OrderCancelled` events via [app/core/queue.py](app/core/queue.py)'s `publish_event` instead of calling notification in-process — the order module no longer depends on notification at all.
* **Consumer**: [app/worker.py](app/worker.py) (`python -m app.worker`) long-polls the queue and dispatches every event to all four handlers — Notification, Email (new `send_email` in [app/notification/service.py](app/notification/service.py)), Analytics ([app/analytics/service.py](app/analytics/service.py)), Search indexing ([app/search/service.py](app/search/service.py)) — with a Redis-backed idempotency guard against SQS's at-least-once redelivery.
* **Retry/DLQ**: no manual retry loop — a failed message is simply left un-deleted and SQS's own visibility timeout makes it visible again; after 5 attempts it's moved automatically to a dead-letter queue ([infra/modules/sqs](infra/modules/sqs)).
* **LocalStack locally**: exposes a real SQS API, so `app/core/queue.py`/`app/worker.py` use the exact same `boto3` calls locally and in AWS — same "swappable backend, same interface" pattern as Postgres (Docker vs. RDS) and Redis (Docker vs. ElastiCache).
* **Independent worker scaling**: a separate ECS service (no ALB, no inbound security group rules at all) scaled by SQS queue depth via step-scaling + CloudWatch alarms ([infra/modules/autoscaling](infra/modules/autoscaling)) rather than CPU/memory/request count — the concrete resolution of [ADR-003](docs/adr/ADR-003-horizontal-scaling.md)'s earlier "scaling on SQS queue depth — not applicable yet" note.
* **Backpressure experiment**: [loadtest/queue_experiment.py](loadtest/queue_experiment.py) produces a 5,000-event burst against a single, artificially slow (~500/sec) consumer, then scales workers to drain the resulting backlog — see [docs/deployment.md](docs/deployment.md#8-v4-asynchronous-processing).
* **Why LocalStack over RabbitMQ, why a separate worker service, best-effort publish's failure mode, and explicit answers to visibility timeout / retry / DLQ / at-least-once delivery / idempotency / backpressure**: [ADR-005](docs/adr/ADR-005-async-processing.md).

## Roadmap

* [x] V0 — Local Modular Monolith
* [x] V1 — AWS Foundation
* [x] V2 — Horizontal Scaling
* [x] V3 — Redis Caching
* [x] V4 — Asynchronous Processing (SQS)
* [ ] V5+ — Reliability, HA, Microservices, Event-Driven Architecture, Kafka, CQRS, Outbox, Saga, AI Operations Agent, Observability, Security, Disaster Recovery, Cost Optimization

Per the [learning project plan](../Solution%20Architect%20Learning%20Project.md).
