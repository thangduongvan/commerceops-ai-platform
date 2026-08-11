### V7 (Microservices): Product / Order / Payment ECS services + worker.
### Service Connect provides DNS discovery (product / payment hostnames) for
### Order's sync HTTP clients. See docs/adr/ADR-008-microservices.md.

resource "aws_service_discovery_http_namespace" "this" {
  name        = "${var.name}.local"
  description = "V7 Service Connect namespace for CommerceOps microservices"
  tags        = var.tags
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.this.arn
  }

  tags = merge(var.tags, { Name = "${var.name}-cluster" })
}

locals {
  common_secrets = [
    { name = "DB_USERNAME", valueFrom = "${var.rds_secret_arn}:username::" },
    { name = "DB_PASSWORD", valueFrom = "${var.rds_secret_arn}:password::" },
  ]

  reliability_env = [
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
    { name = "DB_POOL_RECYCLE_SECONDS", value = tostring(var.db_pool_recycle_seconds) },
  ]
}

# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "product" {
  family                   = "${var.name}-product"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "product"
      image     = var.image
      essential = true
      command   = ["uvicorn", "app.product.main:app", "--host", "0.0.0.0", "--port", tostring(var.container_port)]

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
          name          = "http"
          appProtocol   = "http"
        }
      ]

      environment = concat(
        [
          { name = "SERVICE_NAME", value = "product" },
          { name = "APP_NAME", value = var.app_name },
          { name = "ENVIRONMENT", value = var.environment },
          { name = "DB_HOST", value = var.db_host },
          { name = "DB_PORT", value = tostring(var.db_port) },
          { name = "DB_NAME", value = var.product_db_name },
          { name = "REDIS_HOST", value = var.redis_host },
          { name = "REDIS_PORT", value = tostring(var.redis_port) },
          { name = "CACHE_TTL_SECONDS", value = tostring(var.cache_ttl_seconds) },
          { name = "CACHE_ENABLED", value = tostring(var.cache_enabled) },
          { name = "DB_READ_HOST", value = coalesce(var.db_read_host, "") },
          { name = "READ_REPLICA_ENABLED", value = tostring(var.read_replica_enabled && var.db_read_host != null && var.db_read_host != "") },
        ],
        local.reliability_env,
      )

      secrets = local.common_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "product"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_ecs_service" "product" {
  name            = "${var.name}-product"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.product.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.product_target_group_arn
    container_name   = "product"
    container_port   = var.container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    service {
      port_name      = "http"
      discovery_name = "product"
      client_alias {
        port     = var.container_port
        dns_name = "product"
      }
    }
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(var.tags, { Name = "${var.name}-product" })
}

# ---------------------------------------------------------------------------
# Payment (gateway sidecar stays here — only Payment talks to the fake GW)
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "payment" {
  family                   = "${var.name}-payment"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "payment"
      image     = var.image
      essential = true
      command   = ["uvicorn", "app.payment.main:app", "--host", "0.0.0.0", "--port", tostring(var.container_port)]

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
          name          = "http"
          appProtocol   = "http"
        }
      ]

      environment = concat(
        [
          { name = "SERVICE_NAME", value = "payment" },
          { name = "APP_NAME", value = var.app_name },
          { name = "ENVIRONMENT", value = var.environment },
          { name = "DB_HOST", value = var.db_host },
          { name = "DB_PORT", value = tostring(var.db_port) },
          { name = "DB_NAME", value = var.payment_db_name },
          { name = "READ_REPLICA_ENABLED", value = "false" },
          { name = "PAYMENT_GATEWAY_URL", value = "http://localhost:${var.payment_gateway_port}" },
        ],
        local.reliability_env,
      )

      secrets = local.common_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "payment"
        }
      }
    },
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

resource "aws_ecs_service" "payment" {
  name            = "${var.name}-payment"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.payment.arn
  desired_count   = var.payment_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.payment_target_group_arn
    container_name   = "payment"
    container_port   = var.container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    service {
      port_name      = "http"
      discovery_name = "payment"
      client_alias {
        port     = var.container_port
        dns_name = "payment"
      }
    }
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(var.tags, { Name = "${var.name}-payment" })
}

# ---------------------------------------------------------------------------
# Order (owns customers; calls product + payment via Service Connect DNS)
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "order" {
  family                   = "${var.name}-order"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "order"
      image     = var.image
      essential = true
      command   = ["uvicorn", "app.order.main:app", "--host", "0.0.0.0", "--port", tostring(var.container_port)]

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
          name          = "http"
          appProtocol   = "http"
        }
      ]

      environment = concat(
        [
          { name = "SERVICE_NAME", value = "order" },
          { name = "APP_NAME", value = var.app_name },
          { name = "ENVIRONMENT", value = var.environment },
          { name = "DB_HOST", value = var.db_host },
          { name = "DB_PORT", value = tostring(var.db_port) },
          { name = "DB_NAME", value = var.order_db_name },
          { name = "REDIS_HOST", value = var.redis_host },
          { name = "REDIS_PORT", value = tostring(var.redis_port) },
          { name = "AWS_REGION", value = var.region },
          { name = "SQS_QUEUE_NAME", value = var.sqs_queue_name },
          { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
          { name = "READ_REPLICA_ENABLED", value = "false" },
          # Service Connect DNS names registered by the product/payment services.
          { name = "PRODUCT_SERVICE_URL", value = "http://product:${var.container_port}" },
          { name = "PAYMENT_SERVICE_URL", value = "http://payment:${var.container_port}" },
        ],
        local.reliability_env,
      )

      secrets = local.common_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "order"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_ecs_service" "order" {
  name            = "${var.name}-order"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.order.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.order_target_group_arn
    container_name   = "order"
    container_port   = var.container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    service {
      port_name      = "http"
      discovery_name = "order"
      client_alias {
        port     = var.container_port
        dns_name = "order"
      }
    }
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(var.tags, { Name = "${var.name}-order" })
}

# ---------------------------------------------------------------------------
# Worker (Order DB + SQS)
# ---------------------------------------------------------------------------

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
        { name = "SERVICE_NAME", value = "worker" },
        { name = "APP_NAME", value = var.app_name },
        { name = "ENVIRONMENT", value = var.environment },
        { name = "REDIS_HOST", value = var.redis_host },
        { name = "REDIS_PORT", value = tostring(var.redis_port) },
        { name = "AWS_REGION", value = var.region },
        { name = "SQS_QUEUE_NAME", value = var.sqs_queue_name },
        { name = "SQS_DLQ_NAME", value = var.sqs_dlq_name },
        { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
        { name = "DB_HOST", value = var.db_host },
        { name = "DB_PORT", value = tostring(var.db_port) },
        { name = "DB_NAME", value = var.order_db_name },
        { name = "DB_CONNECT_TIMEOUT_SECONDS", value = tostring(var.db_connect_timeout_seconds) },
        { name = "DB_STATEMENT_TIMEOUT_SECONDS", value = tostring(var.db_statement_timeout_seconds) },
        { name = "WORKER_HANDLER_RETRY_ATTEMPTS", value = tostring(var.worker_handler_retry_attempts) },
        { name = "IDEMPOTENCY_LEASE_TTL_SECONDS", value = tostring(var.sqs_visibility_timeout_seconds) },
        { name = "READ_REPLICA_ENABLED", value = "false" },
        { name = "DB_POOL_RECYCLE_SECONDS", value = tostring(var.db_pool_recycle_seconds) },
      ]

      secrets = local.common_secrets

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

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(var.tags, { Name = "${var.name}-worker" })
}
