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

# V5 — Reliability (see docs/adr/ADR-006-reliability.md)

variable "sqs_dlq_name" {
  description = "Name of the order-events dead-letter queue. The worker needs it to park poison messages directly (app/worker.py), rather than burning max_receive_count redeliveries on a body that can never parse."
  type        = string
}

variable "sqs_visibility_timeout_seconds" {
  description = "Passed to the app and worker so the worker's in-process retry budget and the lease TTL stay consistent with the queue's actual configuration"
  type        = number
  default     = 60
}

variable "payment_gateway_port" {
  description = "Port the fake_gateway sidecar listens on. Reached over localhost from the app container, since awsvpc gives containers in a task a shared network namespace."
  type        = number
  default     = 9000
}

variable "payment_gateway_success_rate" {
  description = "Fraction of charges the stand-in gateway approves under normal conditions. Fault injection is applied at runtime via its /admin/chaos endpoint, not here."
  type        = number
  default     = 0.8
}

variable "payment_connect_timeout_seconds" {
  type    = number
  default = 1.0
}

variable "payment_read_timeout_seconds" {
  description = "How long to wait for the gateway's answer. Exceeding it produces an UNKNOWN outcome (the charge may have happened), not a failure — see PAYMENT_PENDING in app/order/models.py."
  type        = number
  default     = 2.0
}

variable "payment_retry_attempts" {
  description = "Total attempts per charge. 4 with base 1.0 and multiplier 2.0 is the 1s / 2s / 4s ladder."
  type        = number
  default     = 4
}

variable "retry_base_delay_seconds" {
  type    = number
  default = 1.0
}

variable "retry_max_delay_seconds" {
  type    = number
  default = 8.0
}

variable "circuit_breaker_failure_threshold" {
  description = "Consecutive failures before the payment circuit opens and calls fail fast instead of paying the full retry budget"
  type        = number
  default     = 5
}

variable "circuit_breaker_recovery_seconds" {
  description = "How long the circuit stays open before admitting one trial call"
  type        = number
  default     = 30
}

variable "payment_bulkhead_max_concurrency" {
  description = "Cap on concurrent gateway calls, so a hanging gateway can't consume FastAPI's whole thread pool and take unrelated endpoints down with it"
  type        = number
  default     = 10
}

variable "db_connect_timeout_seconds" {
  type    = number
  default = 5
}

variable "db_statement_timeout_seconds" {
  description = "Server-side PostgreSQL statement_timeout. Without it, one hung query holds a pool connection indefinitely and enough of them starve the whole service."
  type        = number
  default     = 5
}

variable "worker_handler_retry_attempts" {
  description = "In-process attempts per handler in app/worker.py. Deliberately smaller than the payment ladder: the whole budget has to fit inside the visibility timeout, and SQS redelivery already provides the slow retries."
  type        = number
  default     = 3
}

# V6 — Database HA (see docs/adr/ADR-007-database-ha.md)

variable "db_read_host" {
  description = "Read-replica hostname for product reads. Empty/null means the app uses the primary for everything."
  type        = string
  default     = null
}

variable "read_replica_enabled" {
  description = "When true, product GET endpoints use DB_READ_HOST. The worker always stays on the primary (it writes processed_events)."
  type        = bool
  default     = false
}

variable "db_pool_recycle_seconds" {
  description = "SQLAlchemy pool_recycle. Bounds how long a pooled connection to a pre-failover primary can linger after Multi-AZ failover."
  type        = number
  default     = 300
}

variable "tags" {
  type    = map(string)
  default = {}
}
