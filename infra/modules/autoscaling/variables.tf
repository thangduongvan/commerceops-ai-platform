variable "name" {
  description = "Name prefix for Auto Scaling resources"
  type        = string
}

variable "ecs_cluster_name" {
  type = string
}

variable "ecs_service_name" {
  type = string
}

variable "min_capacity" {
  description = "Minimum number of ECS tasks. Kept >= 2 so losing one task never drops the service to zero."
  type        = number
  default     = 2
}

variable "max_capacity" {
  description = "Maximum number of ECS tasks. Deliberately bounded by the RDS connection budget, not just cost (see ADR-003)."
  type        = number
  default     = 8
}

variable "cpu_target_value" {
  description = "Target average CPU utilization (%) for the CPU target-tracking policy"
  type        = number
  default     = 60
}

variable "memory_target_value" {
  description = "Target average memory utilization (%) for the memory target-tracking policy"
  type        = number
  default     = 70
}

variable "request_count_target_value" {
  description = "Target ALB requests per second per task for the request-count target-tracking policy"
  type        = number
  default     = 300
}

variable "alb_arn_suffix" {
  description = "ARN suffix of the ALB, used to build the ALBRequestCountPerTarget resource label"
  type        = string
}

variable "target_group_arn_suffix" {
  description = "ARN suffix of the target group, used to build the ALBRequestCountPerTarget resource label"
  type        = string
}

variable "scale_in_cooldown" {
  description = "Seconds to wait after a scale-in before allowing another one"
  type        = number
  default     = 300
}

variable "scale_out_cooldown" {
  description = "Seconds to wait after a scale-out before allowing another one"
  type        = number
  default     = 60
}

# V4 — Asynchronous Processing (see docs/adr/ADR-005-async-processing.md)

variable "worker_ecs_service_name" {
  description = "Name of the worker ECS service (created by infra/modules/ecs)"
  type        = string
}

variable "sqs_queue_name" {
  description = "Name of the order-events queue, for the ApproximateNumberOfMessagesVisible alarm dimension"
  type        = string
}

variable "sns_topic_arn" {
  description = "SNS topic (from infra/modules/cloudwatch) to also notify when the worker scales out"
  type        = string
}

variable "worker_min_capacity" {
  description = "Floor for worker Auto Scaling"
  type        = number
  default     = 1
}

variable "worker_max_capacity" {
  description = "Ceiling for worker Auto Scaling"
  type        = number
  default     = 10
}

variable "worker_scale_out_step" {
  description = "Tasks added per scale-out alarm evaluation"
  type        = number
  default     = 2
}

variable "worker_scale_in_step" {
  description = "Tasks removed per scale-in alarm evaluation"
  type        = number
  default     = 1
}

variable "worker_scale_out_cooldown" {
  type    = number
  default = 60
}

variable "worker_scale_in_cooldown" {
  type    = number
  default = 300
}

variable "worker_queue_depth_high_threshold" {
  description = "ApproximateNumberOfMessagesVisible above which the worker scales out"
  type        = number
  default     = 100
}

variable "worker_queue_depth_low_threshold" {
  description = "ApproximateNumberOfMessagesVisible below which (sustained) the worker scales back in"
  type        = number
  default     = 5
}

# Note: aws_appautoscaling_target/aws_appautoscaling_policy don't support a
# "tags" argument, so this module intentionally has no tags variable.
