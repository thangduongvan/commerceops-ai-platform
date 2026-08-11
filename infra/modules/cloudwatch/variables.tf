variable "name" {
  description = "Name prefix for CloudWatch resources"
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "ecs_cluster_name" {
  description = "Name the ECS module will give the cluster (passed as a plain string to avoid a module dependency cycle)"
  type        = string
}

variable "ecs_service_name" {
  description = "Name the ECS module will give the service (plain string, see ecs_cluster_name)"
  type        = string
}

variable "alb_arn_suffix" {
  type = string
}

variable "target_group_arn_suffix" {
  type = string
}

variable "rds_instance_identifier" {
  description = "RDS instance identifier, for the DB-tier alarms added in V2 (see ADR-003)"
  type        = string
}

variable "rds_cpu_threshold" {
  description = "RDS CPUUtilization (%) above which the DB-tier-is-the-bottleneck alarm fires"
  type        = number
  default     = 80
}

variable "rds_connections_threshold" {
  description = "RDS DatabaseConnections count above which the connection-budget alarm fires. Should be set below max_connections minus headroom (see ADR-003's pool-sizing math)."
  type        = number
  default     = 80
}

variable "redis_cluster_id" {
  description = "ElastiCache cluster id, for the Redis-tier alarms added in V3 (see ADR-004)"
  type        = string
}

variable "redis_cpu_threshold" {
  description = "ElastiCache Redis CPUUtilization (%) above which the cache-tier alarm fires"
  type        = number
  default     = 80
}

variable "redis_evictions_threshold" {
  description = "ElastiCache Redis Evictions (count/period) above which the memory-pressure alarm fires"
  type        = number
  default     = 0
}

variable "alarm_email" {
  description = "Email address to notify on alarm. Leave empty to skip the SNS subscription (topic is still created)."
  type        = string
  default     = ""
}

variable "dlq_name" {
  description = "Name of the order-events DLQ (V4), for the dlq_messages_present alarm"
  type        = string
}

# V5 — Reliability (see docs/adr/ADR-006-reliability.md)

variable "sqs_queue_name" {
  description = "Name of the order-events queue, for the oldest-message-age alarm"
  type        = string
}

variable "queue_max_message_age_seconds" {
  description = "Age of the oldest order-events message above which the queue is considered stuck rather than busy. Adding workers cannot fix this, so it pages instead of scaling."
  type        = number
  default     = 300
}

variable "payment_unavailable_threshold" {
  description = "Orders per minute finishing without a payment answer (PAYMENT_PENDING) before alarming"
  type        = number
  default     = 5
}

variable "tags" {
  type    = map(string)
  default = {}
}
