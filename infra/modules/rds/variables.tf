variable "name" {
  description = "Name prefix for RDS resources"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the DB subnet group"
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group ID controlling inbound access to the DB"
  type        = string
}

variable "db_name" {
  description = "Initial database name"
  type        = string
  default     = "commerceops"
}

variable "db_username" {
  description = "Master username"
  type        = string
  default     = "commerceops"
}

variable "instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "allocated_storage" {
  description = "Allocated storage in GB"
  type        = number
  default     = 20
}

variable "engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "16"
}

variable "backup_retention_period" {
  description = "Days to retain automated backups"
  type        = number
  default     = 7
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
