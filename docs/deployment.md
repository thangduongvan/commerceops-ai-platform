# V1 Deployment Guide — AWS Foundation

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

This creates the VPC, ALB, ECS cluster/service, RDS instance, ECR repo, S3 buckets, IAM roles, and CloudWatch resources. **The ECS service will show 0/1 healthy tasks after this apply** — there's no image in ECR yet, so tasks can't start. That's expected; fixed in the next step.

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

## 6. Wire up GitHub Actions CI/CD

In the GitHub repo: **Settings → Secrets and variables → Actions → Variables**, add:

| Variable | Value |
|---|---|
| `AWS_ROLE_ARN` | `terraform -chdir=infra/environments/dev output -raw github_actions_role_arn` |
| `AWS_REGION` | `us-east-1` (or whatever you used) |
| `ECR_REPOSITORY` | `commerceops-dev-app` |
| `ECS_CLUSTER` | `terraform -chdir=infra/environments/dev output -raw ecs_cluster_name` |
| `ECS_SERVICE` | `terraform -chdir=infra/environments/dev output -raw ecs_service_name` |

No AWS access keys are stored in GitHub — [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) assumes `AWS_ROLE_ARN` via GitHub's OIDC token. From now on, pushes to `main` touching `app/**`/`Dockerfile` automatically build, push, and redeploy.

## 7. Cost control: tear it down when you're done

Per the learning project's "deploy → test → destroy" principle (avoid paying for idle ALB/NAT/RDS between sessions):

```powershell
cd infra/environments/dev
terraform destroy
```

Leave the `infra/bootstrap` state bucket/lock table in place (they cost effectively nothing) so you don't have to redo step 1 next time. Re-running steps 3-6 later recreates everything from the same Terraform code.

## Troubleshooting

* **Tasks stuck in `PENDING`/`STOPPED` immediately after `terraform apply`**: expected before step 4 — no image in ECR yet. Check `aws ecs describe-tasks` / the CloudWatch log group for the actual failure reason if it persists after pushing an image.
* **ALB returns 503**: target group has no healthy targets yet — check ECS service events (`aws ecs describe-services --cluster ... --services ...`) and the `/health` path/port match the app's actual listen port (8000).
* **`terraform init` backend error**: double check `backend.tf` has the exact bucket/table names from `infra/bootstrap` outputs, and that your AWS credentials can access them.
