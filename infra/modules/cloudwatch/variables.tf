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

variable "alarm_email" {
  description = "Email address to notify on alarm. Leave empty to skip the SNS subscription (topic is still created)."
  type        = string
  default     = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
