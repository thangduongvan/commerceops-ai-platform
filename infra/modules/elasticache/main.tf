### V3 note: single node, no Multi-AZ/automatic failover — the same
### "cheap, easy to destroy/recreate while learning" trade-off V1's RDS
### module makes. The consequence is different here, though: Redis is not
### the source of truth (see docs/adr/ADR-004-caching.md), so losing this
### node degrades read latency (the app falls back to Postgres), not
### correctness or availability like losing the single RDS instance would.
### Cache-tier HA (replication group with automatic failover) can be
### revisited alongside V6 (Database HA) if that trade-off stops being
### acceptable.

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis-subnets"
  subnet_ids = var.private_subnet_ids

  tags = var.tags
}

resource "aws_elasticache_cluster" "this" {
  cluster_id      = "${var.name}-redis"
  engine          = "redis"
  engine_version  = var.engine_version
  node_type       = var.node_type
  num_cache_nodes = 1
  port            = 6379

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [var.security_group_id]

  apply_immediately = true

  tags = merge(var.tags, { Name = "${var.name}-redis" })
}
