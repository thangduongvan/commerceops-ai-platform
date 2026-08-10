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
