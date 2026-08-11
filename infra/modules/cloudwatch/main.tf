resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}-app"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"

  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

### "Basic monitoring" per the V1 deliverables list: CPU/memory pressure on the
### service, and two ALB-facing signals (5xx rate, unhealthy targets) that catch
### problems the ECS-level metrics alone would miss.

resource "aws_cloudwatch_metric_alarm" "ecs_cpu_high" {
  alarm_name          = "${var.name}-ecs-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "ECS service average CPU utilization > 80% for 3 minutes"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_service_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "ecs_memory_high" {
  alarm_name          = "${var.name}-ecs-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "ECS service average memory utilization > 80% for 3 minutes"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_service_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx_high" {
  alarm_name          = "${var.name}-alb-5xx-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "More than 10 target 5xx responses in 1 minute"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }

  tags = var.tags
}

### V2: DB-tier alarms. When the app tier scales out under load, these are
### what reveal the database — not compute — becoming the actual bottleneck
### (the "why doesn't app scaling solve DB scaling?" question in ADR-003).

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "${var.name}-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = var.rds_cpu_threshold
  alarm_description   = "RDS CPU utilization > ${var.rds_cpu_threshold}% for 3 minutes — DB tier, not app tier, is now the bottleneck"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_identifier
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_connections_high" {
  alarm_name          = "${var.name}-rds-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Maximum"
  threshold           = var.rds_connections_threshold
  alarm_description   = "RDS open connections > ${var.rds_connections_threshold} for 2 minutes — approaching the connection budget scaled-out ECS tasks share (see ADR-003)"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_identifier
  }

  tags = var.tags
}

### V3: Redis-tier alarms. Surface the new cache's health the same way V2
### did for RDS. High CPU/evictions here don't threaten correctness (Redis
### isn't the source of truth), but they do mean the cache is losing keys
### faster than expected — worth knowing before it shows up as increased
### DB load instead (see docs/adr/ADR-004-caching.md).

resource "aws_cloudwatch_metric_alarm" "redis_cpu_high" {
  alarm_name          = "${var.name}-redis-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Average"
  threshold           = var.redis_cpu_threshold
  alarm_description   = "ElastiCache Redis CPU utilization > ${var.redis_cpu_threshold}% for 3 minutes"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    CacheClusterId = var.redis_cluster_id
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "redis_evictions_high" {
  alarm_name          = "${var.name}-redis-evictions-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Sum"
  threshold           = var.redis_evictions_threshold
  alarm_description   = "ElastiCache Redis is evicting keys under memory pressure — cache is smaller than its working set"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    CacheClusterId = var.redis_cluster_id
  }

  tags = var.tags
}

### V4: any message reaching the DLQ means it failed sqs_max_receive_count
### times in a row — worth paging on immediately, since (unlike the
### queue-depth alarms in infra/modules/autoscaling, which just trigger
### more workers) more workers can't fix a message that's already been
### tried and failed repeatedly. See docs/adr/ADR-005-async-processing.md.

resource "aws_cloudwatch_metric_alarm" "dlq_messages_present" {
  alarm_name          = "${var.name}-order-events-dlq-not-empty"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "At least one message in the order-events DLQ — something is failing repeatedly, not just slow"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = var.dlq_name
  }

  tags = var.tags
}

### V5: queue depth alone cannot distinguish "busy" from "stuck". A backlog of
### 500 messages draining steadily is healthy; a backlog of 3 whose oldest
### message is 20 minutes old means something is wedged — a handler failing
### repeatedly, or no consumer running at all. The autoscaling alarms in
### infra/modules/autoscaling react to depth (add workers); this one reacts to
### *age*, where adding workers won't help.

resource "aws_cloudwatch_metric_alarm" "queue_oldest_message_age_high" {
  alarm_name          = "${var.name}-order-events-oldest-message-age-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = var.queue_max_message_age_seconds
  alarm_description   = "Oldest order-events message older than ${var.queue_max_message_age_seconds}s — the queue is stuck, not merely busy (see ADR-006)"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = var.sqs_queue_name
  }

  tags = var.tags
}

### V5: application-level reliability signals, not infrastructure metrics.
###
### Every alarm above this point watches something AWS measures for us — CPU,
### connections, queue depth. But an open circuit breaker or an exhausted
### retry budget is invisible at that layer: the ECS task is healthy, CPU is
### low, the ALB sees 200s (orders come back as PAYMENT_PENDING, not 500s). The
### only place that knowledge exists is the application's own logs, so we
### extract metrics from them. The log line shapes these filters match are
### emitted by app/core/reliability.py and app/payment/gateway_client.py —
### changing those strings breaks these alarms silently, which is the standing
### trade-off of log-derived metrics (proper instrumentation is V16's job).

resource "aws_cloudwatch_log_metric_filter" "circuit_breaker_open" {
  name           = "${var.name}-circuit-breaker-open"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "circuit_breaker state=OPEN"

  metric_transformation {
    name          = "CircuitBreakerOpen"
    namespace     = "CommerceOps/Reliability"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "circuit_breaker_open" {
  alarm_name          = "${var.name}-circuit-breaker-open"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CircuitBreakerOpen"
  namespace           = "CommerceOps/Reliability"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "A circuit breaker opened — a dependency is failing consistently enough that the app has stopped calling it"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"

  tags = var.tags
}

resource "aws_cloudwatch_log_metric_filter" "payment_gateway_unavailable" {
  name           = "${var.name}-payment-gateway-unavailable"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "payment_gateway_unavailable"

  metric_transformation {
    name          = "PaymentGatewayUnavailable"
    namespace     = "CommerceOps/Reliability"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "payment_gateway_unavailable" {
  alarm_name          = "${var.name}-payment-gateway-unavailable"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "PaymentGatewayUnavailable"
  namespace           = "CommerceOps/Reliability"
  period              = 60
  statistic           = "Sum"
  threshold           = var.payment_unavailable_threshold
  alarm_description   = "More than ${var.payment_unavailable_threshold} orders per minute finished without a payment answer (timeout/open circuit/shed) — these are PAYMENT_PENDING orders awaiting reconciliation, and they are invisible to ALB 5xx and ECS CPU alarms"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_hosts" {
  alarm_name          = "${var.name}-alb-unhealthy-hosts"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "At least one unhealthy target behind the ALB for 2 minutes"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  tags = var.tags
}
