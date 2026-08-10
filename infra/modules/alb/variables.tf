variable "name" {
  description = "Name prefix for ALB resources"
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "health_check_path" {
  type    = string
  default = "/health"
}

variable "access_logs_bucket" {
  description = "S3 bucket name for ALB access logs"
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
