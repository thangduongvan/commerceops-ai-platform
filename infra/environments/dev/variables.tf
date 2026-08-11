variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "commerceops"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "azs" {
  description = "Two availability zones to spread subnets across"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.0.0/24", "10.0.1.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.10.0/24", "10.0.11.0/24"]
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "db_port" {
  type    = number
  default = 5432
}

variable "db_name" {
  description = "Bootstrap RDS database created with the instance. V7 services create their own logical DBs at startup."
  type        = string
  default     = "commerceops"
}

variable "product_db_name" {
  type    = string
  default = "commerceops_product"
}

variable "order_db_name" {
  type    = string
  default = "commerceops_order"
}

variable "payment_db_name" {
  type    = string
  default = "commerceops_payment"
}

variable "payment_desired_count" {
  type    = number
  default = 1
}

variable "db_username" {
  type    = string
  default = "commerceops"
}

variable "rds_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "ecs_cpu" {
  type    = number
  default = 256
}

variable "ecs_memory" {
  type    = number
  default = 512
}

variable "ecs_desired_count" {
  description = "Initial task count. From V2 onward, Auto Scaling adjusts this at runtime (see infra/modules/ecs's ignore_changes on desired_count)."
  type        = number
  default     = 2
}

# V2 — Horizontal Scaling (see docs/adr/ADR-003-horizontal-scaling.md)

variable "ecs_min_capacity" {
  description = "Floor for Auto Scaling. >= 2 so a single task failure never drops the service to zero."
  type        = number
  default     = 2
}

variable "ecs_max_capacity" {
  description = "Ceiling for Auto Scaling. Deliberately bounded by the RDS connection budget (db_pool_size + db_max_overflow per task), not just cost — see ADR-003."
  type        = number
  default     = 8
}

variable "ecs_cpu_target_value" {
  description = "Target average CPU utilization (%) for the CPU target-tracking policy"
  type        = number
  default     = 60
}

variable "ecs_memory_target_value" {
  description = "Target average memory utilization (%) for the memory target-tracking policy"
  type        = number
  default     = 70
}

variable "ecs_request_count_target_value" {
  description = "Target ALB requests/sec/task for the request-count target-tracking policy"
  type        = number
  default     = 300
}

# V3 — Caching (see docs/adr/ADR-004-caching.md)

variable "redis_node_type" {
  description = "ElastiCache Redis node instance type"
  type        = string
  default     = "cache.t3.micro"
}

variable "cache_ttl_seconds" {
  description = "TTL for cached product reads (detail + listing). Bounds staleness on the un-invalidated listing cache — see ADR-004."
  type        = number
  default     = 15
}

variable "cache_enabled" {
  description = "Toggle for the V3 'without cache vs with cache' experiment. Set false to bypass Redis entirely without touching infra."
  type        = bool
  default     = true
}

# V4 — Asynchronous Processing (see docs/adr/ADR-005-async-processing.md)

variable "sqs_visibility_timeout_seconds" {
  description = "How long a received order-events message is hidden before becoming visible again if not deleted (SQS's own retry mechanism). V5 raised this from 30s: the worker's in-process retry ladder must fit inside the window, or the message is redelivered to a second worker mid-retry."
  type        = number
  default     = 60
}

variable "sqs_max_receive_count" {
  description = "Delivery attempts before a message moves to the DLQ"
  type        = number
  default     = 5
}

variable "worker_desired_count" {
  description = "Initial worker task count. From this point on, queue-depth-driven Auto Scaling (infra/modules/autoscaling) manages it — same ignore_changes pattern as the app service's desired_count."
  type        = number
  default     = 1
}

variable "worker_min_capacity" {
  description = "Floor for worker Auto Scaling. >= 1 so the queue is never left completely undrained."
  type        = number
  default     = 1
}

variable "worker_max_capacity" {
  description = "Ceiling for worker Auto Scaling"
  type        = number
  default     = 10
}

variable "github_repo" {
  description = "GitHub repo allowed to deploy via OIDC, in \"owner/repo\" form"
  type        = string
  default     = "thangduongvan/commerceops-ai-platform"
}

variable "github_branch" {
  type    = string
  default = "main"
}

variable "alarm_email" {
  description = "Email to notify on CloudWatch alarms. Leave empty to skip the subscription."
  type        = string
  default     = ""
}

# V5 — Reliability (see docs/adr/ADR-006-reliability.md)

variable "sqs_receive_wait_time_seconds" {
  description = "Queue-level long polling, so a consumer that omits WaitTimeSeconds still waits instead of returning empty in a tight loop"
  type        = number
  default     = 20
}

variable "queue_max_message_age_seconds" {
  description = "Age of the oldest order-events message above which the queue is stuck rather than busy. Unlike depth, adding workers cannot fix this, so it pages instead of scaling."
  type        = number
  default     = 300
}

variable "payment_unavailable_threshold" {
  description = "Orders per minute finishing without a payment answer (PAYMENT_PENDING) before alarming"
  type        = number
  default     = 5
}

variable "payment_gateway_success_rate" {
  description = "Fraction of charges the stand-in gateway approves under normal conditions. Fault injection happens at runtime via its /admin/chaos endpoint (loadtest/chaos_experiment.py), not here."
  type        = number
  default     = 0.8
}

variable "payment_connect_timeout_seconds" {
  description = "TCP connect timeout on the payment gateway call. A connect failure means nothing is listening — unambiguous, and safe to retry."
  type        = number
  default     = 1.0
}

variable "payment_read_timeout_seconds" {
  description = "How long to wait for the gateway's answer. Exceeding it yields an UNKNOWN outcome (the charge may have happened), not a failure — see PAYMENT_PENDING in app/order/models.py."
  type        = number
  default     = 2.0
}

variable "payment_retry_attempts" {
  description = "Total attempts per charge. 4, with base 1.0s and multiplier 2.0, is the 1s / 2s / 4s ladder the V5 spec asks for."
  type        = number
  default     = 4
}

variable "circuit_breaker_failure_threshold" {
  description = "Consecutive failures before the payment circuit opens and calls fail fast instead of each paying the full retry budget"
  type        = number
  default     = 5
}

variable "circuit_breaker_recovery_seconds" {
  description = "How long the circuit stays open before admitting a single trial call"
  type        = number
  default     = 30
}

variable "payment_bulkhead_max_concurrency" {
  description = "Cap on concurrent gateway calls. Roughly a quarter of FastAPI's default thread pool, so a hanging gateway can never consume every thread and take product reads down with it."
  type        = number
  default     = 10
}

variable "db_statement_timeout_seconds" {
  description = "Server-side PostgreSQL statement_timeout. pool_pre_ping detects a dead connection; only this stops a query that connected fine and then ran forever holding a pool slot."
  type        = number
  default     = 5
}

variable "worker_handler_retry_attempts" {
  description = "In-process attempts per handler in app/worker.py. Deliberately smaller than the payment ladder: the budget must fit inside the visibility timeout, and SQS redelivery already provides the slow retries."
  type        = number
  default     = 3
}

# V6 — Database HA (see docs/adr/ADR-007-database-ha.md)
#
# Multi-AZ roughly doubles the RDS instance cost; the replica adds a third.
# Defaults are the safe/learning ones. For cheap terraform destroy cycles set
# rds_multi_az=false, rds_read_replica_enabled=false,
# rds_deletion_protection=false, rds_skip_final_snapshot=true — see
# docs/deployment.md §10.

variable "rds_multi_az" {
  description = "Synchronous standby in another AZ. RPO ≈ 0 for AZ/instance failure; ~2x instance cost."
  type        = bool
  default     = true
}

variable "rds_deletion_protection" {
  description = "Block accidental deletion. Teardown is then two steps (disable, then destroy)."
  type        = bool
  default     = true
}

variable "rds_skip_final_snapshot" {
  description = "When false, take a final snapshot on destroy."
  type        = bool
  default     = false
}

variable "rds_read_replica_enabled" {
  description = "Create an asynchronous same-region read replica for product-read scaling. Not an HA mechanism."
  type        = bool
  default     = true
}

variable "rds_replica_instance_class" {
  description = "Instance class for the read replica"
  type        = string
  default     = "db.t3.micro"
}

variable "rds_replica_lag_threshold_seconds" {
  description = "ReplicaLag (seconds) above which the CloudWatch alarm fires"
  type        = number
  default     = 30
}

variable "read_replica_unavailable_threshold" {
  description = "App log lines of read_replica_unavailable per minute before alarming"
  type        = number
  default     = 5
}

variable "db_pool_recycle_seconds" {
  description = "SQLAlchemy pool_recycle — bounds how long a pooled connection to a pre-failover primary can linger"
  type        = number
  default     = 300
}
