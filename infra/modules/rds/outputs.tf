output "endpoint" {
  description = "Connection endpoint, e.g. host:port"
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "Hostname only (no port)"
  value       = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "db_name" {
  value = aws_db_instance.this.db_name
}

output "master_user_secret_arn" {
  description = "ARN of the Secrets Manager secret AWS created for the master password"
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "identifier" {
  description = "DB instance identifier, used to dimension CloudWatch alarms (e.g. DatabaseConnections)"
  value       = aws_db_instance.this.identifier
}

output "arn" {
  description = "Primary instance ARN — used as replicate_source_db when the replica also sets a subnet group"
  value       = aws_db_instance.this.arn
}

# Null-safe for count = 0: one([]) is null, one([x]) is x.

output "replica_address" {
  description = "Read-replica hostname, or null when read_replica_enabled is false"
  value       = one(aws_db_instance.replica[*].address)
}

output "replica_port" {
  description = "Read-replica port, or null when read_replica_enabled is false"
  value       = one(aws_db_instance.replica[*].port)
}

output "replica_identifier" {
  description = "Read-replica identifier for CloudWatch ReplicaLag alarms, or null when disabled"
  value       = one(aws_db_instance.replica[*].identifier)
}

output "replica_endpoint" {
  description = "Read-replica endpoint (host:port), or null when disabled"
  value       = one(aws_db_instance.replica[*].endpoint)
}
