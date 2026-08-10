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

# Note: aws_appautoscaling_target/aws_appautoscaling_policy don't support a
# "tags" argument, so this module intentionally has no tags variable.
