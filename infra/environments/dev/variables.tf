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
  type    = string
  default = "commerceops"
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
  description = "How long a received order-events message is hidden before becoming visible again if not deleted (SQS's own retry mechanism)"
  type        = number
  default     = 30
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
