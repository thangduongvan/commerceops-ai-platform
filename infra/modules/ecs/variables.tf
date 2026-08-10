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

variable "tags" {
  type    = map(string)
  default = {}
}
