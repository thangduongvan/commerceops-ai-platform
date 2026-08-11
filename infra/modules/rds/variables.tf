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
  description = "Days to retain automated backups (also enables PITR). 0 disables backups."
  type        = number
  default     = 7
}

# V6 — Database HA (see docs/adr/ADR-007-database-ha.md)

variable "multi_az" {
  description = "Deploy a synchronous standby in another AZ. RPO ≈ 0 for AZ/instance failure; roughly doubles the instance cost."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Block accidental deletion. Teardown then requires a two-step disable-then-destroy — deliberate, see docs/deployment.md §10."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "When false, take a final snapshot on destroy (requires final_snapshot_identifier)."
  type        = bool
  default     = false
}

variable "backup_window" {
  description = "Daily automated-backup window (UTC). Kept clear of the maintenance window."
  type        = string
  default     = "03:00-04:00"
}

variable "maintenance_window" {
  description = "Weekly maintenance window (UTC). Must not overlap the backup window."
  type        = string
  default     = "sun:04:00-sun:05:00"
}

variable "apply_immediately" {
  description = "Apply modifications (e.g. flipping multi_az for a drill) immediately rather than during the next maintenance window."
  type        = bool
  default     = true
}

variable "read_replica_enabled" {
  description = "Create an asynchronous same-region read replica for product-read scaling. Not an HA mechanism — see ADR-007."
  type        = bool
  default     = true
}

variable "replica_instance_class" {
  description = "Instance class for the read replica. Defaults to the same class as the primary."
  type        = string
  default     = "db.t3.micro"
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
