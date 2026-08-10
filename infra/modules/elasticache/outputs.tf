output "address" {
  description = "Hostname of the cache node (no port)"
  value       = aws_elasticache_cluster.this.cache_nodes[0].address
}

output "port" {
  value = aws_elasticache_cluster.this.cache_nodes[0].port
}

output "cluster_id" {
  description = "Cache cluster identifier, used to dimension CloudWatch alarms (e.g. Evictions)"
  value       = aws_elasticache_cluster.this.cluster_id
}
