### V1 note: single-AZ, no deletion protection, skip_final_snapshot = true.
### This is a deliberate trade-off for a learning project that needs cheap,
### easy terraform destroy cycles (see Solution Architect doc section 25).
### Multi-AZ / PITR-driven DR is addressed properly in V6 (Database HA) and
### V18 (Disaster Recovery) — this is documented in ADR-002.

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
  multi_az               = false

  backup_retention_period = var.backup_retention_period
  skip_final_snapshot     = true
  deletion_protection     = false

  tags = merge(var.tags, { Name = "${var.name}-postgres" })
}
