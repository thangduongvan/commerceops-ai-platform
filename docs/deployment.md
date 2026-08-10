# Deployment Guide — AWS Foundation (V1) + Horizontal Scaling (V2)

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

This creates the VPC, ALB, ECS cluster/service (+ Auto Scaling target/policies, per [ADR-003](adr/ADR-003-horizontal-scaling.md)), RDS instance, ECR repo, S3 buckets, IAM roles, and CloudWatch resources. **The ECS service will show 0/2 healthy tasks after this apply** — there's no image in ECR yet, so tasks can't start. That's expected; fixed in the next step.

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

Then force the service to pick it up:

```powershell
$CLUSTER = terraform -chdir=infra/environments/dev output -raw ecs_cluster_name
$SERVICE = terraform -chdir=infra/environments/dev output -raw ecs_service_name

aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE
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

## 7. Wire up GitHub Actions CI/CD

In the GitHub repo: **Settings → Secrets and variables → Actions → Variables**, add:

| Variable | Value |
|---|---|
| `AWS_ROLE_ARN` | `terraform -chdir=infra/environments/dev output -raw github_actions_role_arn` |
| `AWS_REGION` | `us-east-1` (or whatever you used) |
| `ECR_REPOSITORY` | `commerceops-dev-app` |
| `ECS_CLUSTER` | `terraform -chdir=infra/environments/dev output -raw ecs_cluster_name` |
| `ECS_SERVICE` | `terraform -chdir=infra/environments/dev output -raw ecs_service_name` |

No AWS access keys are stored in GitHub — [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) assumes `AWS_ROLE_ARN` via GitHub's OIDC token. From now on, pushes to `main` touching `app/**`/`Dockerfile` automatically build, push, and redeploy.

## 8. Cost control: tear it down when you're done

Per the learning project's "deploy → test → destroy" principle (avoid paying for idle ALB/NAT/RDS between sessions):

```powershell
cd infra/environments/dev
terraform destroy
```

Leave the `infra/bootstrap` state bucket/lock table in place (they cost effectively nothing) so you don't have to redo step 1 next time. Re-running steps 3-7 later recreates everything from the same Terraform code.

## Troubleshooting

* **Tasks stuck in `PENDING`/`STOPPED` immediately after `terraform apply`**: expected before step 4 — no image in ECR yet. Check `aws ecs describe-tasks` / the CloudWatch log group for the actual failure reason if it persists after pushing an image.
* **ALB returns 503**: target group has no healthy targets yet — check ECS service events (`aws ecs describe-services --cluster ... --services ...`) and the `/health` path/port match the app's actual listen port (8000).
* **`terraform init` backend error**: double check `backend.tf` has the exact bucket/table names from `infra/bootstrap` outputs, and that your AWS credentials can access them.
