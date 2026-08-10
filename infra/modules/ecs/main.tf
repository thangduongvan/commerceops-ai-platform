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
