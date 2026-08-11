### Security group chain: internet -> ALB -> ECS tasks -> RDS / Redis.
### Each tier only accepts traffic from the tier directly in front of it
### (security-group-to-security-group references, no wide-open CIDRs beyond the ALB).

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb-sg"
  description = "Allow inbound HTTP from the internet to the ALB"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTP from internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-alb-sg" })
}

resource "aws_security_group" "ecs" {
  name        = "${var.name}-ecs-sg"
  description = "Allow inbound app traffic from the ALB and east-west Service Connect"
  vpc_id      = var.vpc_id

  ingress {
    description     = "App port from ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # V7: Order → Product / Payment over ECS Service Connect (same SG).
  ingress {
    description = "East-west Service Connect between microservices"
    from_port   = var.container_port
    to_port     = var.container_port
    protocol    = "tcp"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-ecs-sg" })
}

resource "aws_security_group" "rds" {
  name        = "${var.name}-rds-sg"
  description = "Allow inbound PostgreSQL from ECS tasks and the worker only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from ECS tasks"
    from_port       = var.db_port
    to_port         = var.db_port
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  # V5 (Reliability): the worker now writes the processed_events table
  # (app/core/idempotency.py), because Redis alone cannot be the authority on
  # whether a side effect already ran — a key that can be evicted or lost on
  # restart stops deduplicating under exactly the conditions that cause
  # redeliveries. V4's worker deliberately had no DB access at all, so without
  # this rule the worker would work locally and fail only in AWS.
  ingress {
    description     = "Postgres from the worker"
    from_port       = var.db_port
    to_port         = var.db_port
    protocol        = "tcp"
    security_groups = [aws_security_group.worker.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-rds-sg" })
}

resource "aws_security_group" "redis" {
  name        = "${var.name}-redis-sg"
  description = "Allow inbound Redis from ECS tasks and the worker only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Redis from ECS tasks"
    from_port       = var.redis_port
    to_port         = var.redis_port
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  # V4: the worker task also reads Redis directly (app/core/cache.py's
  # mark_event_processed idempotency guard), so it needs the same access.
  ingress {
    description     = "Redis from the worker"
    from_port       = var.redis_port
    to_port         = var.redis_port
    protocol        = "tcp"
    security_groups = [aws_security_group.worker.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-redis-sg" })
}

### V4: the worker (app/worker.py) never accepts inbound traffic at all —
### it only calls out to SQS, Redis, and (as of V5) RDS — so it gets no
### ingress rules whatsoever, unlike every other tier above. A cheap, explicit
### illustration that not every compute tier needs an open inbound port.

resource "aws_security_group" "worker" {
  name        = "${var.name}-worker-sg"
  description = "Worker task: no inbound traffic accepted, egress only (SQS, Redis, RDS)"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name}-worker-sg" })
}
