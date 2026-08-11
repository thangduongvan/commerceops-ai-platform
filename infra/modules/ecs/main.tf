resource "aws_ecs_cluster" "this" {
  name = "${var.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(var.tags, { Name = "${var.name}-cluster" })
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${var.name}-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.image
      essential = true

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "APP_NAME", value = var.app_name },
        { name = "ENVIRONMENT", value = var.environment },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_PORT", value = tostring(var.db_port) },
        { name = "DB_NAME", value = var.db_name },
        # V3 (Caching): no `secrets` entry needed here — this ElastiCache
        # cluster has no AUTH token/TLS (see docs/adr/ADR-004-caching.md),
        # so host/port are as safe to log/inspect as DB_HOST/DB_PORT above.
        { name = "REDIS_HOST", value = var.redis_host },
        { name = "REDIS_PORT", value = tostring(var.redis_port) },
        { name = "CACHE_TTL_SECONDS", value = tostring(var.cache_ttl_seconds) },
        { name = "CACHE_ENABLED", value = tostring(var.cache_enabled) },
        # V4 (Asynchronous Processing): no SQS_ENDPOINT_URL here — unset
        # means boto3 talks to the real regional SQS endpoint, authenticated
        # via this task's own IAM role (var.task_role_arn), no static keys.
        { name = "AWS_REGION", value = var.region },
        { name = "SQS_QUEUE_NAME", value = var.sqs_queue_name },
        { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
        # V5 (Reliability): localhost, because the payment gateway runs as a
        # sidecar in this same task (below) and awsvpc network mode gives the
        # containers in a task a shared network namespace. Under Docker Compose
        # this is http://payment-gateway:9000 — the only value that differs
        # between the two environments, the same shape as SQS_ENDPOINT_URL.
        { name = "PAYMENT_GATEWAY_URL", value = "http://localhost:${var.payment_gateway_port}" },
        { name = "PAYMENT_CONNECT_TIMEOUT_SECONDS", value = tostring(var.payment_connect_timeout_seconds) },
        { name = "PAYMENT_READ_TIMEOUT_SECONDS", value = tostring(var.payment_read_timeout_seconds) },
        { name = "PAYMENT_RETRY_ATTEMPTS", value = tostring(var.payment_retry_attempts) },
        { name = "RETRY_BASE_DELAY_SECONDS", value = tostring(var.retry_base_delay_seconds) },
        { name = "RETRY_MAX_DELAY_SECONDS", value = tostring(var.retry_max_delay_seconds) },
        { name = "CIRCUIT_BREAKER_FAILURE_THRESHOLD", value = tostring(var.circuit_breaker_failure_threshold) },
        { name = "CIRCUIT_BREAKER_RECOVERY_SECONDS", value = tostring(var.circuit_breaker_recovery_seconds) },
        { name = "PAYMENT_BULKHEAD_MAX_CONCURRENCY", value = tostring(var.payment_bulkhead_max_concurrency) },
        { name = "DB_CONNECT_TIMEOUT_SECONDS", value = tostring(var.db_connect_timeout_seconds) },
        { name = "DB_STATEMENT_TIMEOUT_SECONDS", value = tostring(var.db_statement_timeout_seconds) },
      ]

      # Only the credentials come from Secrets Manager — host/port/dbname are not
      # sensitive and are easier to read in the task definition / logs while debugging.
      secrets = [
        { name = "DB_USERNAME", valueFrom = "${var.rds_secret_arn}:username::" },
        { name = "DB_PASSWORD", valueFrom = "${var.rds_secret_arn}:password::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "app"
        }
      }
    },
    # V5 (Reliability): the stand-in third-party payment gateway
    # (fake_gateway/), as a sidecar rather than its own service. A sidecar
    # needs no service discovery, no load balancer, and no extra Fargate task
    # — the app reaches it on localhost. Inventing internal service discovery
    # for a fake dependency would be V7's (Microservices) work done early for
    # no benefit; a real integration deletes this container and points
    # PAYMENT_GATEWAY_URL at the provider's public endpoint.
    #
    # essential = false is the important part: if this stand-in crashes or is
    # killed during a chaos experiment, ECS must NOT tear down the whole task.
    # The app is supposed to survive its payment provider dying — that is the
    # entire thesis of this version, and an essential sidecar would prove the
    # opposite by taking the API down with it.
    {
      name      = "payment-gateway"
      image     = var.image
      essential = false
      command   = ["uvicorn", "fake_gateway.main:app", "--host", "0.0.0.0", "--port", tostring(var.payment_gateway_port)]

      portMappings = [
        {
          containerPort = var.payment_gateway_port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "GATEWAY_SUCCESS_RATE", value = tostring(var.payment_gateway_success_rate) },
        { name = "GATEWAY_ERROR_RATE", value = "0" },
        { name = "GATEWAY_HANG_RATE", value = "0" },
        { name = "GATEWAY_LATENCY_MS", value = "0" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "payment-gateway"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_ecs_service" "app" {
  name            = "${var.name}-app"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [var.security_group_id]
    # No public IP: tasks live in private subnets and reach the internet (e.g. ECR)
    # via the NAT Gateway; inbound traffic only arrives via the ALB.
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "app"
    container_port   = var.container_port
  }

  # Note: the image tag is always "latest" (see var.image) and CI/CD redeploys via
  # `aws ecs update-service --force-new-deployment` rather than registering a new
  # task definition revision, so there is nothing for Terraform to fight over here.
  # (Trade-off documented in ADR-002: no per-deploy task-def history/easy rollback,
  # acceptable for a V1 learning project.)

  # V2: Application Auto Scaling (infra/modules/autoscaling) owns desired_count
  # after the initial apply — it raises/lowers it directly via the ECS API in
  # response to CPU/memory/request-count targets. Without ignore_changes here,
  # the next `terraform apply` would see the drift between the static
  # `var.desired_count` and whatever Auto Scaling set at runtime, and reset it
  # back down, fighting the very thing this version is trying to build.
  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(var.tags, { Name = "${var.name}-app" })
}

### V4 (Asynchronous Processing): a second, separate ECS service running
### app/worker.py — the order-events consumer. Same image as "app", but no
### load_balancer block (it's not behind the ALB, doesn't accept inbound
### traffic at all — see the worker security group). Scaled independently from
### the API tier, driven by queue depth rather than CPU/memory/request count
### (infra/modules/autoscaling).
###
### V5 (Reliability) added the RDS/Secrets Manager wiring V4 deliberately
### omitted. The worker now writes the processed_events table
### (app/core/idempotency.py): the durable record that a given side effect
### already ran. Redis stays in front of it as a cache and an in-flight lease,
### but it can't be the authority — a key that can be evicted or lost on
### restart stops deduplicating under exactly the conditions (failover,
### partition, restart) that cause redeliveries in the first place. Also gets
### no payment-gateway sidecar: the worker's handlers never call it.

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.worker_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.image
      essential = true
      command   = ["python", "-m", "app.worker"]

      environment = [
        { name = "APP_NAME", value = var.app_name },
        { name = "ENVIRONMENT", value = var.environment },
        { name = "REDIS_HOST", value = var.redis_host },
        { name = "REDIS_PORT", value = tostring(var.redis_port) },
        { name = "AWS_REGION", value = var.region },
        { name = "SQS_QUEUE_NAME", value = var.sqs_queue_name },
        { name = "SQS_DLQ_NAME", value = var.sqs_dlq_name },
        { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
        # V5: the worker's own DB access, for processed_events.
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_PORT", value = tostring(var.db_port) },
        { name = "DB_NAME", value = var.db_name },
        { name = "DB_CONNECT_TIMEOUT_SECONDS", value = tostring(var.db_connect_timeout_seconds) },
        { name = "DB_STATEMENT_TIMEOUT_SECONDS", value = tostring(var.db_statement_timeout_seconds) },
        { name = "WORKER_HANDLER_RETRY_ATTEMPTS", value = tostring(var.worker_handler_retry_attempts) },
        { name = "IDEMPOTENCY_LEASE_TTL_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
      ]

      secrets = [
        { name = "DB_USERNAME", valueFrom = "${var.rds_secret_arn}:username::" },
        { name = "DB_PASSWORD", valueFrom = "${var.rds_secret_arn}:password::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "worker"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.worker_security_group_id]
    assign_public_ip = false
  }

  # V4: infra/modules/autoscaling's step-scaling policies (driven by SQS
  # queue-depth CloudWatch alarms, not CPU/memory/ALB) own desired_count
  # after the initial apply — same drift-avoidance reasoning as the app
  # service above.
  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(var.tags, { Name = "${var.name}-worker" })
}
