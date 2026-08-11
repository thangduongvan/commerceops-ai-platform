### V6 (Database HA): Multi-AZ instance deployment for AZ/instance failure
### (synchronous standby, RPO ≈ 0, automatic failover via the same DNS
### endpoint), automated backups + PITR for accidental deletion (which
### Multi-AZ cannot help with — the DELETE reaches the standby too), and an
### optional asynchronous read replica for read scaling. See
### docs/adr/ADR-007-database-ha.md for HA ≠ Backup ≠ Read Replica ≠ DR.
###
### Module defaults are the *safe* ones (multi_az / deletion_protection on,
### final snapshot taken). The env stack exposes them as variables so a
### learning project can still flip them off for cheap terraform destroy
### cycles — see docs/deployment.md §10.

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-db-subnets"
  subnet_ids = var.private_subnet_ids

  tags = merge(var.tags, { Name = "${var.name}-db-subnets" })
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name}-postgres"
  engine         = "postgres"
  engine_version = var.engine_version

  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.db_username

  # AWS creates and rotates a Secrets Manager secret for the master password
  # instead of us ever holding a plaintext password in Terraform state or a
  # tfvars file (see ADR-002 for the alternatives considered).
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false
  multi_az               = var.multi_az

  backup_retention_period = var.backup_retention_period
  backup_window           = var.backup_window
  maintenance_window      = var.maintenance_window
  copy_tags_to_snapshot   = true

  # apply_immediately so flipping multi_az for a failover drill (or turning
  # deletion_protection off for teardown) does not wait for the next
  # maintenance window. Accept the brief disruption in a learning project.
  apply_immediately = var.apply_immediately

  # V6: take a final snapshot on destroy by default. skip_final_snapshot and
  # deletion_protection are variables so teardown can still be two deliberate
  # steps rather than an accidental `terraform destroy` wiping confirmed
  # orders — see docs/deployment.md §10.
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name}-postgres-final"
  deletion_protection       = var.deletion_protection

  tags = merge(var.tags, { Name = "${var.name}-postgres" })
}

### Asynchronous read replica. Own endpoint, own lag, does *not* auto-failover.
### Promoting it loses whatever had not yet replayed — a scalability tool, not
### an HA one. Credentials / db_name / engine are inherited via physical
### replication and must not be set here (Terraform errors on the conflict).
### Because we also set db_subnet_group_name, replicate_source_db must be the
### source ARN, not its identifier.

resource "aws_db_instance" "replica" {
  count = var.read_replica_enabled ? 1 : 0

  identifier          = "${var.name}-postgres-replica"
  replicate_source_db = aws_db_instance.this.arn
  instance_class      = var.replica_instance_class

  storage_encrypted = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = false
  multi_az               = false

  # Replicas do not take their own automated backups; the primary's backups
  # cover PITR. A final snapshot of a replica is also not useful for restore.
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = var.apply_immediately

  tags = merge(var.tags, { Name = "${var.name}-postgres-replica" })
}
