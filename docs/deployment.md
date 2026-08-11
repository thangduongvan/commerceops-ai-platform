# Deployment Guide — AWS Foundation (V1) + Horizontal Scaling (V2) + Caching (V3) + Asynchronous Processing (V4) + Reliability (V5)

Step-by-step commands to deploy CommerceOps to AWS (ECS/Fargate + ALB + RDS) from a Windows machine using PowerShell. All Terraform lives under [infra/](../infra/).

Nothing in this guide has been run for you — no AWS CLI, Terraform, or AWS credentials are available in the environment that authored this code. Run every step yourself and review the plan output before each `apply`.

## 0. Prerequisites

Install the AWS CLI v2 and Terraform:

```powershell
winget install -e --id Amazon.AWSCLI
winget install -e --id Hashicorp.Terraform
```

Open a new PowerShell window (so PATH updates take effect), then configure credentials for an IAM user/role with sufficient permissions (VPC, ECS, ECR, RDS, ALB, IAM, S3, CloudWatch, Secrets Manager, DynamoDB):

```powershell
aws configure
aws sts get-caller-identity   # sanity check
```

## 1. Bootstrap the Terraform state backend (one-time per AWS account)

```powershell
cd infra/bootstrap
terraform init
terraform apply
```

Note the two outputs — you need them in the next step:

```powershell
terraform output state_bucket
terraform output lock_table
```

## 2. Point `environments/dev` at that backend

Edit [infra/environments/dev/backend.tf](../infra/environments/dev/backend.tf) and replace `REPLACE_WITH_BOOTSTRAP_STATE_BUCKET` / `REPLACE_WITH_BOOTSTRAP_LOCK_TABLE` with the two values from step 1 (and `region` if you're not using `us-east-1`).

Then copy the tfvars example and adjust `github_repo` if you forked this repo:

```powershell
cd ../environments/dev
copy terraform.tfvars.example terraform.tfvars
```

## 3. Provision the infrastructure

```powershell
terraform init
terraform plan
terraform apply
```

This creates the VPC, ALB, ECS cluster/service (+ Auto Scaling target/policies, per [ADR-003](adr/ADR-003-horizontal-scaling.md)), RDS instance, ElastiCache Redis cluster (per [ADR-004](adr/ADR-004-caching.md)), the `order-events` SQS queue + DLQ and worker ECS service (per [ADR-005](adr/ADR-005-async-processing.md)), ECR repo, S3 buckets, IAM roles, and CloudWatch resources. **The ECS service will show 0/2 healthy tasks after this apply** — there's no image in ECR yet, so tasks can't start. That's expected; fixed in the next step.

Save the outputs — you'll need them below:

```powershell
terraform output
```

## 4. Push the first image (chicken-and-egg step)

ECS can't run a task until an image exists in ECR, and the ECR repo doesn't exist until step 3 runs. Build and push manually the first time:

```powershell
cd ../../..   # back to the commerceops-ai-platform repo root

$ECR_REPO = terraform -chdir=infra/environments/dev output -raw ecr_repository_url
$REGION   = "us-east-1"

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ($ECR_REPO -split '/')[0]

docker build -t "${ECR_REPO}:latest" .
docker push "${ECR_REPO}:latest"
```

Then force the services to pick it up — both the app and the worker (V4) run the same image:

```powershell
$CLUSTER = terraform -chdir=infra/environments/dev output -raw ecs_cluster_name
$SERVICE = terraform -chdir=infra/environments/dev output -raw ecs_service_name
$WORKER  = terraform -chdir=infra/environments/dev output -raw worker_service_name

aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment
aws ecs update-service --cluster $CLUSTER --service $WORKER --force-new-deployment
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE $WORKER
```

## 5. Verify

```powershell
$ALB_DNS = terraform -chdir=infra/environments/dev output -raw alb_dns_name
curl "http://$ALB_DNS/health"
curl "http://$ALB_DNS/docs"   # Swagger UI
```

Check logs in CloudWatch: log group `/ecs/commerceops-dev-app` (or run `terraform -chdir=infra/environments/dev output cloudwatch_log_group`).

## 6. V2: Load testing (horizontal scaling)

This step exercises the Auto Scaling added in V2 — see [ADR-003](adr/ADR-003-horizontal-scaling.md) for why it's built this way. It requires the stack from steps 1-5 to already be up.

Install the load-test tool (kept separate from the app's own `requirements.txt`):

```powershell
cd loadtest
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the staged flash-sale experiment the learning project spec calls for — 100 / 500 / 1,000 / 5,000 req/s — against the ALB. Each stage below approximates the target req/s with concurrent users at the locustfile's `wait_time` of 1-3s per action (roughly `users / 2` req/s); adjust `-u`/`-r` if your own run doesn't match:

```powershell
$ALB_DNS = terraform -chdir=infra/environments/dev output -raw alb_dns_name

# Baseline (~100 req/s)
locust -f locustfile.py --host "http://$ALB_DNS" --headless -u 200 -r 20 --run-time 5m --csv stage-100

# ~500 req/s
locust -f locustfile.py --host "http://$ALB_DNS" --headless -u 1000 -r 50 --run-time 5m --csv stage-500

# ~1,000 req/s
locust -f locustfile.py --host "http://$ALB_DNS" --headless -u 2000 -r 100 --run-time 5m --csv stage-1000

# Flash-sale peak (~5,000 req/s) for the full 10-minute duration from the spec
locust -f locustfile.py --host "http://$ALB_DNS" --headless -u 10000 -r 200 --run-time 10m --csv stage-5000
```

While a stage runs, watch (in separate terminals) whether Auto Scaling is actually reacting, and where the bottleneck moves to as load increases:

```powershell
# ECS desired vs. running task count, updated every few seconds
while ($true) {
  aws ecs describe-services --cluster $CLUSTER --services $SERVICE `
    --query "services[0].{desired:desiredCount,running:runningCount}"
  Start-Sleep -Seconds 10
}
```

* **CloudWatch → Container Insights**: per-task/service CPU and memory.
* **CloudWatch → Alarms**: `*-ecs-cpu-high` / `*-ecs-memory-high` (app tier) vs. the new `*-rds-cpu-high` / `*-rds-connections-high` (DB tier) — per ADR-003, expect the DB-tier alarms to start firing before app scaling alone can fix things.
* **Locust's own output** (or the `stage-*_stats.csv` files): p50/p95 latency, requests/sec actually achieved, and error rate per stage.

Record latency, throughput, CPU/memory, error rate, and DB connection count for each stage — that comparison is the actual deliverable of V2's "Experiment" section in the learning project spec.

## 7. V3: Caching

Adds a Redis cache-aside layer in front of `GET /products` and `GET /products/{id}` — see [ADR-004](adr/ADR-004-caching.md) for the design and the required "what happens when Redis dies?" etc. answers. Requires the stack from steps 1-5 (now also provisioning an ElastiCache cluster).

### 7.1 Experiment: without cache vs with cache

The `CACHE_ENABLED` env var (wired through `infra/environments/dev`'s `cache_enabled` variable, defaulting to `true`) lets you A/B this without touching any other infrastructure:

```powershell
# Baseline: cache OFF
cd infra/environments/dev
terraform apply -var="cache_enabled=false"
$CLUSTER = terraform output -raw ecs_cluster_name
$SERVICE = terraform output -raw ecs_service_name
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE

cd ../../../loadtest
locust -f locustfile.py --host "http://$ALB_DNS" --headless -u 1000 -r 50 --run-time 5m --csv no-cache

# Cache ON — flip it back and rerun the same stage
cd ../infra/environments/dev
terraform apply -var="cache_enabled=true"
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE

cd ../../../loadtest
locust -f locustfile.py --host "http://$ALB_DNS" --headless -u 1000 -r 50 --run-time 5m --csv with-cache
```

Compare, cache off vs on:

* **Locust output / `*_stats.csv`**: p50/p95 latency, achieved req/s.
* **RDS CloudWatch metrics** (`DatabaseConnections`, `ReadIOPS`, `CPUUtilization` — same dashboards/alarms V2 added): DB query load should drop sharply with the cache on, since `list_products` dominates traffic at 90%+ per the flash-sale scenario.

### 7.2 Reading the cache hit ratio

* **Locally** (Docker Compose): `docker compose exec redis redis-cli INFO stats | findstr keyspace` — `keyspace_hits` / (`keyspace_hits` + `keyspace_misses`) is the hit ratio.
* **AWS**: CloudWatch → Metrics → `AWS/ElastiCache` → `CacheHits` / `CacheMisses` for the cluster (`terraform output redis_endpoint` to find it). Expect the loadtest's biased "hot" product subset (see `loadtest/locustfile.py`) to show a much higher hit ratio than the long tail of products.
* **Alarms**: `*-redis-cpu-high` / `*-redis-evictions-high` — evictions firing means the node is smaller than its working set (bump `redis_node_type` if so).

### 7.3 Failure injection: what happens when Redis dies?

Confirms the fallback behavior documented in ADR-004 — the app should keep serving Product reads (just slower, and with more DB load), never error out.

Locally:

```powershell
docker compose stop redis
curl http://localhost:8000/products   # should still return 200
docker compose start redis
```

On AWS, the equivalent is temporarily pointing `REDIS_HOST` at an address nothing is listening on (e.g. via `terraform apply -var="redis_node_type=..."` won't do this — instead, briefly override the ECS task definition's `REDIS_HOST` env var, redeploy, confirm `/products` still returns 200, then revert) or simply stopping/deleting the ElastiCache cluster and rerunning a Locust stage against the ALB.

## 8. V4: Asynchronous Processing

Decouples order creation from Notification/Analytics/Email/Search indexing via the `order-events` SQS queue and a separate worker service — see [ADR-005](adr/ADR-005-async-processing.md) for the design and the required visibility-timeout/retry/DLQ/at-least-once/idempotency/backpressure answers.

### 8.1 Local: run against LocalStack

```powershell
docker compose up --build
```

`localstack` and `worker` are new services alongside `db`/`redis`/`app` — the LocalStack init script (`infra/localstack/create-queues.sh`) creates the `order-events` queue + DLQ automatically on startup. Place an order and watch the worker's logs pick up the fan-out:

```powershell
docker compose logs -f worker
# in another terminal:
curl -X POST http://localhost:8000/customers -H "Content-Type: application/json" -d '{\"name\":\"Dana\",\"email\":\"dana@example.com\"}'
curl -X POST http://localhost:8000/products -H "Content-Type: application/json" -d '{\"name\":\"Widget\",\"price\":9.99,\"stock_quantity\":10}'
curl -X POST http://localhost:8000/orders -H "Content-Type: application/json" -d '{\"customer_id\":1,\"items\":[{\"product_id\":1,\"quantity\":1}]}'
```

The worker's log should show `notification`, `email`, `analytics`, and `search-index` lines for `OrderCreated`, then again for `OrderPaid`/`OrderPaymentFailed`.

### 8.2 Backpressure experiment: produce 5,000/sec, consume ~500/sec, then scale workers

```powershell
cd loadtest
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

$env:AWS_REGION = "us-east-1"
$env:AWS_ACCESS_KEY_ID = "test"
$env:AWS_SECRET_ACCESS_KEY = "test"
$env:SQS_ENDPOINT_URL = "http://localhost:4566"   # omit against real AWS

# Terminal 1: watch the backlog grow, then shrink
python queue_experiment.py depth --watch

# Terminal 2: stop the real worker first (docker compose stop worker), then
# run one artificially slow consumer standing in for it (~500/sec)
python queue_experiment.py consume --delay-ms 2

# Terminal 3: fire the burst
python queue_experiment.py produce --count 5000
```

Watch terminal 1: `visible` climbs while the burst outruns the single slow consumer, then drains back toward 0 once it catches up. To see scaling fix the backlog rather than just waiting it out, stop the single slow consumer mid-drain and instead scale the real workers:

```powershell
docker compose up -d --scale worker=4 worker
```

`visible` should fall noticeably faster with 4 workers than with 1. In AWS, the equivalent is watching the `*-worker-queue-depth-high` CloudWatch alarm trigger the step-scaling policy in [infra/modules/autoscaling](../infra/modules/autoscaling) and the worker service's task count increase (`aws ecs describe-services --cluster $CLUSTER --services $WORKER --query "services[0].{desired:desiredCount,running:runningCount}"`).

### 8.3 Inspecting/redriving the DLQ

Locally:

```powershell
docker compose exec localstack awslocal sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/commerceops-order-events-dlq --attribute-names ApproximateNumberOfMessages
```

In AWS: CloudWatch alarm `*-order-events-dlq-not-empty` fires the moment any message lands in the DLQ (per ADR-005, this means repeated failures, not just a slow consumer — worth investigating before redriving). Once the underlying issue is fixed, redrive DLQ messages back to the main queue with the SQS console's "Start DLQ redrive" action, or `aws sqs start-message-move-task`.

V5 adds a CLI that works identically against LocalStack and real SQS — see §9.6.

## 9. V5: Reliability

Injects the four faults the spec names — payment timeout, 50% API failure, consumer crash, duplicate event — and verifies the system survives each. See [ADR-006](adr/ADR-006-reliability.md) for the design and the required retry/timeout/DLQ/idempotency/circuit-breaker/bulkhead answers.

Everything here is driven by [loadtest/chaos_experiment.py](../loadtest/chaos_experiment.py), which sets up its own test data and prints each scenario's expected outcome before measuring the actual one.

### 9.1 Local setup

```powershell
docker compose up --build
```

`payment-gateway` is the new service (port 9000): a stand-in third-party gateway in its own process, so timeouts and connection failures are real rather than simulated in-process. `worker` also gained a Postgres dependency in V5 — it writes the `processed_events` table.

```powershell
cd loadtest
.venv\Scripts\activate
pip install -r requirements.txt

$env:APP_URL = "http://localhost:8000"
$env:GATEWAY_URL = "http://localhost:9000"
$env:AWS_REGION = "us-east-1"
$env:AWS_ACCESS_KEY_ID = "test"
$env:AWS_SECRET_ACCESS_KEY = "test"
$env:SQS_ENDPOINT_URL = "http://localhost:4566"   # omit against real AWS

python chaos_experiment.py all      # or one scenario at a time, below
```

### 9.2 Payment timeout

```powershell
python chaos_experiment.py timeout
```

Makes the gateway hang for 10s on every request, well past the client's 2s read timeout.

**Expect**: orders come back `PAYMENT_PENDING`, *not* `PAYMENT_FAILED` — the gateway accepted the request and went quiet, so whether the card was charged is genuinely unknown. Stock is **not** released (releasing inventory for a possibly-paid order is the worse error; see ADR-006 §6). The first few orders take ~7s each as the retry ladder plays out; after five consecutive failures the circuit opens and the rest return almost instantly. That drop in latency *is* the circuit breaker working.

To watch it by hand instead:

```powershell
curl -X POST http://localhost:9000/admin/chaos -H "Content-Type: application/json" -d '{\"hang_rate\":1.0,\"hang_ms\":10000}'
curl http://localhost:8000/health/ready        # payment_gateway.circuit_state
docker compose logs app | Select-String "circuit_breaker|payment_gateway_unavailable"
curl -X POST http://localhost:9000/admin/reset
```

### 9.3 50% API failure

```powershell
python chaos_experiment.py failure
```

Makes the gateway return 503 to half of all requests.

**Expect**: clearly *more* than half the orders end up `PAID`. This is the point — a per-request failure rate is not an order failure rate, because a 503 is retried (unlike a decline, which is a business answer and never retried). Latency rises where retries happened. `GET /admin/charges` on the gateway confirms the charge count matches the order count, not the attempt count.

### 9.4 Consumer crash

```powershell
python chaos_experiment.py crash
```

Publishes 200 events, `docker compose kill`s the worker mid-drain (SIGKILL, no graceful shutdown), then restarts it.

**Expect**: no event loss. Messages the worker was holding show up as `in_flight` immediately after the kill, become visible again once the 60s visibility timeout expires, and are processed by the restarted worker. The queue drains to zero. A slow drain here is the visibility timeout, not loss.

### 9.5 Duplicate event

```powershell
python chaos_experiment.py duplicate
```

Publishes the identical `event_id` twice.

**Expect**: exactly one set of business effects. The second delivery finds durable `processed_events` rows and logs `resuming, handlers already done`. Verify directly:

```powershell
docker compose logs worker | Select-String "<event_id from the script output>"
docker compose exec db psql -U commerceops -d commerceops -c "SELECT event_id, handler_name FROM processed_events ORDER BY processed_at DESC LIMIT 8;"
```

Four rows per event — one per handler — is the shape that makes partial failure recoverable: a redelivery re-runs only the handlers that are missing.

### 9.6 Failure isolation (bulkhead)

```powershell
python chaos_experiment.py isolation
```

Hangs the gateway for 30s while hammering `POST /orders` with 30 concurrent requests, and simultaneously polls `GET /products`.

**Expect**: product reads stay fast (p50 in milliseconds) throughout, and some orders are shed immediately with `bulkhead_full` or `circuit_open` rather than queueing. Without the bulkhead, 30 hanging order requests would hold most of FastAPI's ~40-thread pool and product reads — which touch nothing but Redis and Postgres — would start timing out behind a dependency they never use.

### 9.7 DLQ inspection and redrive

```powershell
python -m app.dlq inspect              # depth + a sample of bodies, consumes nothing
python -m app.dlq redrive --max 10     # send back to the main queue, in batches
python -m app.dlq purge --yes          # give up on them (unrecoverable)
```

Redrive only *after* fixing whatever made the messages fail — a message that failed five times will fail five more and land straight back in the DLQ. Run it in batches so a dependency that has only just recovered isn't knocked over by the whole backlog at once.

Against AWS, drop `SQS_ENDPOINT_URL` and set `SQS_QUEUE_NAME`/`SQS_DLQ_NAME` from the Terraform outputs:

```powershell
$env:SQS_QUEUE_NAME = terraform -chdir=infra/environments/dev output -raw sqs_queue_name
$env:SQS_DLQ_NAME = terraform -chdir=infra/environments/dev output -raw sqs_dlq_name
```

### 9.8 The same experiments against AWS

Point `APP_URL` at the ALB and skip `SQS_ENDPOINT_URL`. `GATEWAY_URL` is the one thing that isn't reachable: the gateway runs as a sidecar on the task's `localhost`, with no ALB route. Two options:

* **Chaos via env vars**: set `GATEWAY_HANG_RATE` / `GATEWAY_ERROR_RATE` on the `payment-gateway` container in the task definition and redeploy. Blunter than the admin endpoint (a redeploy per change), but no public surface on a fault-injection endpoint.
* **Kill the sidecar**: `aws ecs execute-command` into the task and stop the gateway process. This is why the sidecar is `essential = false` — killing it must not make ECS tear down the whole task. The app should keep serving reads while orders go `PAYMENT_PENDING`.

The reliability alarms to watch in the CloudWatch console while doing this ([infra/modules/cloudwatch](../infra/modules/cloudwatch)):

| Alarm | Fires when |
|---|---|
| `*-circuit-breaker-open` | A breaker opened — the app has stopped calling a dependency |
| `*-payment-gateway-unavailable` | Orders are completing without a payment answer (`PAYMENT_PENDING` piling up) |
| `*-order-events-oldest-message-age-high` | The queue is *stuck*, not merely busy — adding workers won't help |
| `*-order-events-dlq-not-empty` | Messages have failed repeatedly (V4) |

The first two are log-metric-filter alarms, because an open circuit is invisible to infrastructure metrics: the task is healthy, CPU is low, and the ALB sees 200s.

## 10. V6: Database High Availability

New requirement: `RTO ≤ 5 minutes`, `RPO ≈ 0`, and a database outage must not lose confirmed orders. V6 turns on Multi-AZ, backups/PITR, deletion protection, and an optional read replica — and makes the distinctions between them measurable. See [ADR-007](adr/ADR-007-database-ha.md).

### 10.1 Enable Multi-AZ + replica (and the cost note)

In `infra/environments/dev/terraform.tfvars` (see `terraform.tfvars.example`):

```hcl
rds_multi_az             = true   # ~2x instance cost; synchronous standby
rds_read_replica_enabled = true   # +1x; asynchronous, product reads only
rds_deletion_protection  = true   # teardown becomes two steps (below)
rds_skip_final_snapshot  = false
```

```powershell
cd infra/environments/dev
terraform apply
```

Multi-AZ conversion and replica creation both take several minutes. Afterwards:

```powershell
terraform output rds_endpoint
terraform output rds_replica_endpoint
terraform output rds_identifier
```

The app task receives `DB_READ_HOST` + `READ_REPLICA_ENABLED=true`; the worker keeps `READ_REPLICA_ENABLED=false` (it writes `processed_events`).

For cheap idle periods, flip the four flags off and re-apply before a long break — per the learning project's "deploy temporarily, benchmark, destroy" guidance. Leaving Multi-AZ + replica running overnight on `db.t3.micro` is the single largest avoidable cost in this stack after the NAT Gateway.

### 10.2 Local streaming replication (free drills)

```powershell
docker compose up -d --build
docker compose exec db psql -U commerceops -d commerceops -c "SELECT * FROM pg_stat_replication;"
docker compose exec db-replica psql -U commerceops -d commerceops -c "SELECT pg_is_in_recovery();"
```

Expect a streaming WAL sender on the primary and `t` (in recovery) on the replica. The app's `DATABASE_READ_URL` points at `db-replica`; product GETs use it, orders do not.

### 10.3 Lag drill (read-your-own-writes)

```powershell
cd loadtest
pip install -r requirements.txt
$env:APP_URL = "http://localhost:8000"
python ha_experiment.py lag
```

Writes a product on the primary, polls the replica until it appears, and prints `/health/ready`'s `database_replica.lag_seconds`. The lesson: a customer reading their own order right after placing it **cannot** use this replica — that is why only product GETs are routed there.

### 10.4 Local promote drill (RPO experiment)

```powershell
python ha_experiment.py promote-local --duration 20
```

Places orders continuously, stops the primary halfway through, and promotes the Compose standby with `pg_ctl promote`. Then compares every HTTP-2xx-confirmed order id against what survived on the promoted standby.

* **Async (default)**: some confirmed orders may be missing → RPO > 0.
* **Sync**: append `-c synchronous_standby_names='*'` to the `db` service's `command` in `docker-compose.yml`, recreate, and re-run. Lost orders should go to 0 — and the primary will block commits if the standby is stopped. That is exactly why RDS Multi-AZ needs a healthy standby.

This drill leaves Compose broken (primary stopped, replica promoted). Recreate with:

```powershell
docker compose down -v
docker compose up -d --build
```

### 10.5 AWS Multi-AZ failover drill (RTO experiment)

Requires Multi-AZ enabled (§10.1) and an ALB URL:

```powershell
$env:APP_URL = "http://$(terraform -chdir=../infra/environments/dev output -raw alb_dns_name)"
$env:RDS_INSTANCE_ID = terraform -chdir=../infra/environments/dev output -raw rds_identifier
python ha_experiment.py failover-aws
```

Runs `aws rds reboot-db-instance --force-failover` (Multi-AZ **instance** failover — `failover-db-cluster` is Aurora-only) while polling `/health` and `POST /orders`. Reports measured RTO against the 300s budget and confirms every HTTP-2xx order is still readable afterwards (RPO ≈ 0).

Watch the SNS/email alarm from the RDS event subscription (`failover` category) — a Multi-AZ failover is invisible to CPU / ALB-5xx / unhealthy-host alarms because the endpoint never changed and the tasks stay healthy.

### 10.6 PITR restore drill (why backups ≠ HA)

```powershell
python ha_experiment.py restore-pitr
# review the planned command, then:
python ha_experiment.py restore-pitr --confirm
```

Reads `LatestRestorableTime`, starts `restore-db-instance-to-point-in-time`, and times how long the **new** instance takes to become available. The payoff: tens of minutes and a new endpoint — concrete proof that backups defend against `DELETE`/corruption, not against AZ failure, and cannot meet RTO ≤ 5 minutes.

Delete the target instance afterwards; it is a full billed RDS instance.

### 10.7 Two-step teardown (deletion protection)

With `rds_deletion_protection = true` (the default), a plain `terraform destroy` fails on the RDS instance. That is the point.

```powershell
cd infra/environments/dev
# 1. Disable protection (and optionally skip the final snapshot for a throwaway env)
terraform apply -var="rds_deletion_protection=false" -var="rds_skip_final_snapshot=true"
# 2. Destroy
terraform destroy
```

Or set both in `terraform.tfvars` before destroy. Leaving the bootstrap state bucket in place is still recommended (step 12 below).

## 11. V7: Microservices

Product / Order / Payment run as separate ECS services (and Compose services) with database-per-service. See [ADR-008](adr/ADR-008-microservices.md).

### Local

```powershell
cd commerceops-ai-platform
docker compose up --build -d
# Gateway: http://localhost:8000
# Product :8001  Order :8002  Payment :8003
curl http://localhost:8000/health
curl http://localhost:8000/products
```

Fault-isolation drill:

```powershell
$env:APP_URL = "http://localhost:8000"
python loadtest/microservices_experiment.py fault-isolation
python loadtest/microservices_experiment.py compare-latency
python loadtest/microservices_experiment.py checklist
```

### AWS shape

* **ALB**: path rules `/products*` + `/internal*` → Product TG; `/payments*` → Payment TG; default → Order TG.
* **ECS**: `*-product`, `*-order`, `*-payment`, `*-worker` services; Payment keeps the fake-gateway sidecar.
* **Service Connect**: namespace `${name}.local`; Order uses `PRODUCT_SERVICE_URL=http://product:8000` and `PAYMENT_SERVICE_URL=http://payment:8000`.
* **RDS**: bootstrap DB `commerceops`; each task sets `DB_NAME=commerceops_product|order|payment` and `ensure_database` creates the logical DB on first boot. Master user needs `CREATEDB` (default on RDS).
* **CI**: set GitHub variable `ECS_SERVICES` to a comma-separated list of the four service names (or rely on the workflow's derivation from `ECS_SERVICE`).

After `terraform apply`, force redeploys of all four services once the image is in ECR (same as V1 step 4, but four services).

## 12. Wire up GitHub Actions CI/CD

In the GitHub repo: **Settings → Secrets and variables → Actions → Variables**, add:

| Variable | Value |
|---|---|
| `AWS_ROLE_ARN` | `terraform -chdir=infra/environments/dev output -raw github_actions_role_arn` |
| `AWS_REGION` | `us-east-1` (or whatever you used) |
| `ECR_REPOSITORY` | `commerceops-dev-app` |
| `ECS_CLUSTER` | `terraform -chdir=infra/environments/dev output -raw ecs_cluster_name` |
| `ECS_SERVICE` | Product service name (legacy fallback for the workflow) |
| `ECS_SERVICES` | `commerceops-dev-product,commerceops-dev-order,commerceops-dev-payment,commerceops-dev-worker` |

No AWS access keys are stored in GitHub — [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) assumes `AWS_ROLE_ARN` via GitHub's OIDC token. From now on, pushes to `main` touching `app/**`/`Dockerfile` automatically build, push, and redeploy all four ECS services.

## 13. Cost control: tear it down when you're done

Per the learning project's "deploy → test → destroy" principle (avoid paying for idle ALB/NAT/RDS/ElastiCache/Multi-AZ/replica between sessions).

If V6 deletion protection is on, do the two-step teardown in §10.7 first. Otherwise:

```powershell
cd infra/environments/dev
terraform destroy
```

Leave the `infra/bootstrap` state bucket/lock table in place (they cost effectively nothing) so you don't have to redo step 1 next time. Re-running steps 3–11 later recreates everything from the same Terraform code.

## Troubleshooting

* **Tasks stuck in `PENDING`/`STOPPED` immediately after `terraform apply`**: expected before step 4 — no image in ECR yet. Check `aws ecs describe-tasks` / the CloudWatch log group for the actual failure reason if it persists after pushing an image.
* **ALB returns 503**: target group has no healthy targets yet — check ECS service events for `product` / `order` / `payment` and the `/health` path/port match (8000).
* **`terraform init` backend error**: double check `backend.tf` has the exact bucket/table names from `infra/bootstrap` outputs, and that your AWS credentials can access them.
* **Products endpoints work but seem to ignore recent writes**: check `CACHE_ENABLED` — if `true`, `GET /products` listings are only guaranteed fresh within `cache_ttl_seconds` (see [ADR-004](adr/ADR-004-caching.md)'s "how do you invalidate the cache?"). `GET /products/{id}` is invalidated immediately on update, so if that's stale too, check the app logs for `cache_get_json`/`cache_delete` warnings — likely Redis is unreachable and every request is silently falling back to Postgres.
* **Orders succeed but the worker never logs notification/analytics/email/search lines**: check `docker compose logs order` for `publish_event failed` warnings and `docker compose logs localstack` for whether `create-queues.sh` ran. In AWS, confirm the worker IAM role has `sqs:ReceiveMessage` **and `sqs:GetQueueUrl`**.
* **Every order comes back `PAYMENT_PENDING` (V5)**: Order can't reach Payment, or Payment can't reach the gateway. Check Order `/health/ready` → `payment_service`, and Payment `/health/ready` → `payment_gateway`.
* **Orders return 503 mentioning Product service (V7)**: Product is down or unreachable via Service Connect / Compose DNS. Run `python loadtest/microservices_experiment.py fault-isolation` to see the intended behaviour.
* **`db-replica` / `product-db-replica` never becomes healthy**: recreate volumes with `docker compose down -v && docker compose up -d`. Init scripts live under `infra/postgres/product/`.
* **Product GETs look stale right after a PUT (V6)**: expected under replica lag (and Redis TTL). See [ADR-007](adr/ADR-007-database-ha.md).
* **`terraform destroy` fails on the RDS instance (V6)**: `deletion_protection` is on. Follow §10.7.
* **Failover drill reports RPO > 0 on Multi-AZ (V6)**: unexpected — confirm `MultiAZ = true` and `--force-failover`.
