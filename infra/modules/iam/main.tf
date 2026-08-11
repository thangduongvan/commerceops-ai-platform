data "aws_caller_identity" "current" {}

### --- ECS task execution role ---
### Used by the ECS agent itself: pull the image from ECR, write container logs to
### CloudWatch, and fetch the RDS secret to inject as container "secrets". This is
### distinct from the task role below (least privilege: the agent's permissions are
### not the same as the application's permissions).

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.name}-ecs-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ecs_task_execution_secret" {
  statement {
    sid       = "ReadRdsManagedSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.rds_secret_arn]
  }
}

resource "aws_iam_role_policy" "ecs_task_execution_secret" {
  name   = "${var.name}-read-rds-secret"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ecs_task_execution_secret.json
}

### --- ECS task role ---
### Used by the application code at runtime (AWS SDK calls from inside the container).
### V1 only needs the app-assets S3 bucket (reserved for future use); nothing else yet.

resource "aws_iam_role" "ecs_task" {
  name               = "${var.name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "ecs_task_app" {
  statement {
    sid     = "AppAssetsBucketAccess"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      var.app_assets_bucket_arn,
      "${var.app_assets_bucket_arn}/*",
    ]
  }

  # V4: the app only ever publishes (app/core/queue.py's publish_event) —
  # it never reads or deletes messages, that's the worker's job below.
  # Least privilege: each tier gets exactly the SQS actions it uses.
  #
  # V5 added sqs:GetQueueUrl. Both tiers resolve a queue *name* to its URL at
  # runtime (app/core/queue.py's resolve_queue_url) rather than being handed a
  # pre-built URL, so that call needs authorizing too — it was missing since
  # V4, where it would have surfaced as every publish silently failing in AWS
  # while working perfectly against LocalStack (which doesn't check IAM).
  statement {
    sid       = "PublishOrderEvents"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
    resources = [var.sqs_queue_arn]
  }
}

resource "aws_iam_role_policy" "ecs_task_app" {
  name   = "${var.name}-app-assets-access"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_app.json
}

### --- ECS worker task role ---
### Used by app/worker.py at runtime. Deliberately a separate role from the
### app's task role above, scoped to only the SQS actions the consumer side
### needs — the worker never touches the S3 app-assets bucket, and the app
### never receives/deletes queue messages.

resource "aws_iam_role" "ecs_worker_task" {
  name               = "${var.name}-ecs-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "ecs_worker_task" {
  statement {
    sid = "ConsumeOrderEvents"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      # V5: the worker extends the in-flight window before running its retry
      # ladder, so the message isn't redelivered to a second worker mid-retry
      # (app/worker.py's _extend_visibility).
      "sqs:ChangeMessageVisibility",
    ]
    resources = [var.sqs_queue_arn]
  }

  # V5: poison messages (bodies that can never parse) are sent straight to the
  # DLQ instead of burning max_receive_count redeliveries first. Send-only —
  # the worker never consumes from the DLQ, since draining it is a deliberate
  # operator action (`python -m app.dlq redrive`) that must not happen
  # automatically. A message reaches the DLQ precisely because retrying didn't
  # work, so an automatic drain is just a slower infinite loop.
  statement {
    sid       = "ParkPoisonMessages"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
    resources = [var.sqs_dlq_arn]
  }
}

resource "aws_iam_role_policy" "ecs_worker_task" {
  name   = "${var.name}-consume-order-events"
  role   = aws_iam_role.ecs_worker_task.id
  policy = data.aws_iam_policy_document.ecs_worker_task.json
}

### --- GitHub Actions OIDC role (CI/CD) ---
### No long-lived AWS access keys stored in GitHub: the workflow exchanges a
### short-lived GitHub-issued OIDC token for temporary credentials scoped to this role.

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]

  tags = var.tags
}

locals {
  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_actions_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/${var.github_branch}"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.name}-github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "github_actions_deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = [var.ecr_repository_arn]
  }

  statement {
    sid     = "EcsRedeploy"
    actions = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = [
      for svc in (
        length(var.ecs_service_names) > 0
        ? var.ecs_service_names
        : [var.ecs_service_name]
      ) :
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:service/${var.ecs_cluster_name}/${svc}"
    ]
  }

  statement {
    sid       = "EcsDescribeTaskDefinition"
    actions   = ["ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "${var.name}-github-actions-deploy"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_deploy.json
}
