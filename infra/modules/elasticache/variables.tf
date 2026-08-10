variable "name" {
  description = "Name prefix for ElastiCache resources"
  type        = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "node_type" {
  description = "Cache node instance type"
  type        = string
  default     = "cache.t3.micro"
}

variable "engine_version" {
  type    = string
  default = "7.1"
}

variable "tags" {
  type    = map(string)
  default = {}
}
