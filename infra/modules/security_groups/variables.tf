variable "name" {
  description = "Name prefix for security groups"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID the security groups belong to"
  type        = string
}

variable "container_port" {
  description = "Port the application container listens on"
  type        = number
  default     = 8000
}

variable "db_port" {
  description = "Port PostgreSQL listens on"
  type        = number
  default     = 5432
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
