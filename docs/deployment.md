# Deployment Guide — AWS Foundation (V1) + Horizontal Scaling (V2) + Caching (V3) + Asynchronous Processing (V4)

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

## 9. Wire up GitHub Actions CI/CD

In the GitHub repo: **Settings → Secrets and variables → Actions → Variables**, add:

| Variable | Value |
|---|---|
| `AWS_ROLE_ARN` | `terraform -chdir=infra/environments/dev output -raw github_actions_role_arn` |
| `AWS_REGION` | `us-east-1` (or whatever you used) |
| `ECR_REPOSITORY` | `commerceops-dev-app` |
| `ECS_CLUSTER` | `terraform -chdir=infra/environments/dev output -raw ecs_cluster_name` |
| `ECS_SERVICE` | `terraform -chdir=infra/environments/dev output -raw ecs_service_name` |

No AWS access keys are stored in GitHub — [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) assumes `AWS_ROLE_ARN` via GitHub's OIDC token. From now on, pushes to `main` touching `app/**`/`Dockerfile` automatically build, push, and redeploy.

## 10. Cost control: tear it down when you're done

Per the learning project's "deploy → test → destroy" principle (avoid paying for idle ALB/NAT/RDS/ElastiCache between sessions):

```powershell
cd infra/environments/dev
terraform destroy
```

Leave the `infra/bootstrap` state bucket/lock table in place (they cost effectively nothing) so you don't have to redo step 1 next time. Re-running steps 3-8 later recreates everything from the same Terraform code.

## Troubleshooting

* **Tasks stuck in `PENDING`/`STOPPED` immediately after `terraform apply`**: expected before step 4 — no image in ECR yet. Check `aws ecs describe-tasks` / the CloudWatch log group for the actual failure reason if it persists after pushing an image.
* **ALB returns 503**: target group has no healthy targets yet — check ECS service events (`aws ecs describe-services --cluster ... --services ...`) and the `/health` path/port match the app's actual listen port (8000).
* **`terraform init` backend error**: double check `backend.tf` has the exact bucket/table names from `infra/bootstrap` outputs, and that your AWS credentials can access them.
* **Products endpoints work but seem to ignore recent writes**: check `CACHE_ENABLED` — if `true`, `GET /products` listings are only guaranteed fresh within `cache_ttl_seconds` (see [ADR-004](adr/ADR-004-caching.md)'s "how do you invalidate the cache?"). `GET /products/{id}` is invalidated immediately on update, so if that's stale too, check the app logs for `cache_get_json`/`cache_delete` warnings — likely Redis is unreachable and every request is silently falling back to Postgres.
* **Orders succeed but the worker never logs notification/analytics/email/search lines**: check `docker compose logs app` for `publish_event failed` warnings (queue unreachable — often `localstack` not yet healthy when `app` started) and `docker compose logs localstack` for whether `create-queues.sh` actually ran (look for "V4: order-events queue + DLQ ready on LocalStack"). In AWS, check the worker task's CloudWatch logs and confirm its IAM role has `sqs:ReceiveMessage` on the queue ([ADR-005](adr/ADR-005-async-processing.md)).
