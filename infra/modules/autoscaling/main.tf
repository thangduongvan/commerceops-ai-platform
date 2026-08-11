### V2: ECS Service Auto Scaling. All three policies attach to the same
### scalable target — Application Auto Scaling scales OUT as soon as ANY
### policy's metric breaches its target, and only scales IN once ALL
### policies agree it's safe. This is what lets a sudden request-rate
### spike react faster than the slower-moving CPU/memory averages, while
### still avoiding a scale-in that would violate a CPU/memory target.
###
### `min_capacity`/`max_capacity` double as the DB connection budget guard
### rail (see ADR-003) — this module doesn't just pick numbers for cost.

resource "aws_appautoscaling_target" "ecs" {
  service_namespace  = "ecs"
  resource_id        = "service/${var.ecs_cluster_name}/${var.ecs_service_name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_capacity
  max_capacity       = var.max_capacity
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.name}-cpu-target-tracking"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = var.cpu_target_value
    scale_in_cooldown  = var.scale_in_cooldown
    scale_out_cooldown = var.scale_out_cooldown
  }
}

resource "aws_appautoscaling_policy" "memory" {
  name               = "${var.name}-memory-target-tracking"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = var.memory_target_value
    scale_in_cooldown  = var.scale_in_cooldown
    scale_out_cooldown = var.scale_out_cooldown
  }
}

resource "aws_appautoscaling_policy" "request_count" {
  name               = "${var.name}-request-count-target-tracking"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${var.alb_arn_suffix}/${var.target_group_arn_suffix}"
    }
    target_value       = var.request_count_target_value
    scale_in_cooldown  = var.scale_in_cooldown
    scale_out_cooldown = var.scale_out_cooldown
  }
}

### V4 (Asynchronous Processing): SQS queue-depth-driven worker scaling.
### The three policies above are target tracking, where AWS creates and
### manages the underlying CloudWatch alarms automatically. Application
### Auto Scaling has no predefined metric type for SQS queue depth, so this
### is step scaling instead — this module creates the two alarms itself
### and wires them directly to the step policies below. This is the
### concrete resolution of ADR-003's earlier note deferring "scaling on
### SQS queue depth" to V4.

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${var.ecs_cluster_name}/${var.worker_ecs_service_name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.worker_min_capacity
  max_capacity       = var.worker_max_capacity
}

resource "aws_appautoscaling_policy" "worker_scale_out" {
  name               = "${var.name}-worker-queue-depth-scale-out"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = var.worker_scale_out_cooldown
    metric_aggregation_type = "Average"

    step_adjustment {
      scaling_adjustment          = var.worker_scale_out_step
      metric_interval_lower_bound = 0
    }
  }
}

resource "aws_appautoscaling_policy" "worker_scale_in" {
  name               = "${var.name}-worker-queue-depth-scale-in"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = var.worker_scale_in_cooldown
    metric_aggregation_type = "Average"

    step_adjustment {
      scaling_adjustment          = -var.worker_scale_in_step
      metric_interval_upper_bound = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_queue_depth_high" {
  alarm_name          = "${var.name}-worker-queue-depth-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Average"
  threshold           = var.worker_queue_depth_high_threshold
  alarm_description   = "order-events backlog > ${var.worker_queue_depth_high_threshold} messages for 2 minutes — scale the worker out"
  alarm_actions       = [aws_appautoscaling_policy.worker_scale_out.arn, var.sns_topic_arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = var.sqs_queue_name
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_queue_depth_low" {
  alarm_name          = "${var.name}-worker-queue-depth-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 5
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Average"
  threshold           = var.worker_queue_depth_low_threshold
  alarm_description   = "order-events backlog sustained < ${var.worker_queue_depth_low_threshold} messages for 5 minutes — scale the worker back in"
  alarm_actions       = [aws_appautoscaling_policy.worker_scale_in.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = var.sqs_queue_name
  }
}
