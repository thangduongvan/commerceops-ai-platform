variable "name" {
  description = "Name prefix for ECS resources"
  type        = string
}

variable "region" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "image" {
  description = "Full image reference, e.g. <ecr_repo_url>:latest"
  type        = string
}

variable "cpu" {
  type    = number
  default = 256
}

variable "memory" {
  type    = number
  default = 512
}

variable "desired_count" {
  description = "Initial number of tasks. From V2 onward this is only the starting point — Application Auto Scaling (infra/modules/autoscaling) manages it afterwards and this module ignores drift on it (see lifecycle block below)."
  type        = number
  default     = 2
}

variable "execution_role_arn" {
  type = string
}

variable "task_role_arn" {
  type = string
}

variable "target_group_arn" {
  type = string
}

variable "log_group_name" {
  type = string
}

variable "rds_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the RDS username/password"
  type        = string
}

variable "db_host" {
  type = string
}

variable "db_port" {
  type = number
}

variable "db_name" {
  type = string
}

variable "redis_host" {
  type = string
}

variable "redis_port" {
  type    = number
  default = 6379
}

variable "cache_ttl_seconds" {
  type    = number
  default = 15
}

variable "cache_enabled" {
  type    = bool
  default = true
}

variable "app_name" {
  type    = string
  default = "CommerceOps AI Platform"
}

variable "environment" {
  type    = string
  default = "aws-dev"
}

# V4 — Asynchronous Processing (see docs/adr/ADR-005-async-processing.md)

variable "sqs_queue_name" {
  description = "Name of the order-events queue, resolved to a URL at runtime via get_queue_url (see app/core/queue.py)"
  type        = string
}

variable "worker_task_role_arn" {
  description = "IAM role the worker task assumes (Receive/Delete/GetQueueAttributes on the order-events queue only)"
  type        = string
}

variable "worker_security_group_id" {
  description = "Security group for the worker task (no ingress rules at all)"
  type        = string
}

variable "worker_desired_count" {
  description = "Initial worker task count. Application Auto Scaling manages it afterwards, same ignore_changes pattern as the app service."
  type        = number
  default     = 1
}

variable "worker_cpu" {
  type    = number
  default = 256
}

variable "worker_memory" {
  type    = number
  default = 512
}

variable "tags" {
  type    = map(string)
  default = {}
}
