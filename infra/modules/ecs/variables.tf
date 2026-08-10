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
  description = "Number of tasks. Kept at 1 for V1 — Auto Scaling arrives in V2."
  type        = number
  default     = 1
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
