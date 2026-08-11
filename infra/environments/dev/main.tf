locals {
  name = "${var.project_name}-${var.environment}"

  # ECS cluster/service names, computed the same way the ecs module names them
  # internally. Passed as plain strings to the iam and cloudwatch modules so
  # they can build ARNs / alarm dimensions without depending on the ecs module
  # itself (avoids a module dependency cycle: ecs depends on iam and cloudwatch).
  ecs_cluster_name = "${local.name}-cluster"
  # V7: GitHub Actions / cloudwatch primarily track the Product service
  # (read-heavy flash-sale path). Deploy redeploys all four services.
  ecs_service_name = "${local.name}-product"
  ecs_service_names = [
    "${local.name}-product",
    "${local.name}-order",
    "${local.name}-payment",
    "${local.name}-worker",
  ]

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

module "vpc" {
  source = "../../modules/vpc"

  name                 = local.name
  vpc_cidr             = var.vpc_cidr
  azs                  = var.azs
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  tags                 = local.tags
}

module "security_groups" {
  source = "../../modules/security_groups"

  name           = local.name
  vpc_id         = module.vpc.vpc_id
  container_port = var.container_port
  db_port        = var.db_port
  tags           = local.tags
}

module "ecr" {
  source = "../../modules/ecr"

  name = "${local.name}-app"
  tags = local.tags
}

module "s3" {
  source = "../../modules/s3"

  name = local.name
  tags = local.tags
}

module "rds" {
  source = "../../modules/rds"

  name               = local.name
  private_subnet_ids = module.vpc.private_subnet_ids
  security_group_id  = module.security_groups.rds_sg_id
  db_name            = var.db_name
  db_username        = var.db_username
  instance_class     = var.rds_instance_class

  # V6 (Database HA): Multi-AZ + backups/PITR + optional read replica.
  # Defaults are the safe ones; flip multi_az / read_replica_enabled /
  # deletion_protection off for cheap destroy cycles — see ADR-007 and
  # docs/deployment.md §10.
  multi_az               = var.rds_multi_az
  deletion_protection    = var.rds_deletion_protection
  skip_final_snapshot    = var.rds_skip_final_snapshot
  read_replica_enabled   = var.rds_read_replica_enabled
  replica_instance_class = var.rds_replica_instance_class

  tags = local.tags
}

module "elasticache" {
  source = "../../modules/elasticache"

  name               = local.name
  private_subnet_ids = module.vpc.private_subnet_ids
  security_group_id  = module.security_groups.redis_sg_id
  node_type          = var.redis_node_type
  tags               = local.tags
}

module "sqs" {
  source = "../../modules/sqs"

  name                       = local.name
  visibility_timeout_seconds = var.sqs_visibility_timeout_seconds
  max_receive_count          = var.sqs_max_receive_count
  receive_wait_time_seconds  = var.sqs_receive_wait_time_seconds
  tags                       = local.tags
}

module "iam" {
  source = "../../modules/iam"

  name                  = local.name
  region                = var.region
  rds_secret_arn        = module.rds.master_user_secret_arn
  app_assets_bucket_arn = module.s3.app_assets_bucket_arn
  sqs_queue_arn         = module.sqs.queue_arn
  sqs_dlq_arn           = module.sqs.dlq_arn
  ecr_repository_arn    = module.ecr.repository_arn
  ecs_cluster_name      = local.ecs_cluster_name
  ecs_service_name      = local.ecs_service_name
  ecs_service_names     = local.ecs_service_names
  github_repo           = var.github_repo
  github_branch         = var.github_branch
  tags                  = local.tags
}

module "alb" {
  source = "../../modules/alb"

  name               = local.name
  vpc_id             = module.vpc.vpc_id
  public_subnet_ids  = module.vpc.public_subnet_ids
  security_group_id  = module.security_groups.alb_sg_id
  container_port     = var.container_port
  access_logs_bucket = module.s3.alb_logs_bucket_id
  tags               = local.tags
}

module "cloudwatch" {
  source = "../../modules/cloudwatch"

  name                    = local.name
  ecs_cluster_name        = local.ecs_cluster_name
  ecs_service_name        = local.ecs_service_name
  alb_arn_suffix          = module.alb.arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  rds_instance_identifier = module.rds.identifier
  redis_cluster_id        = module.elasticache.cluster_id
  dlq_name                = module.sqs.dlq_name
  alarm_email             = var.alarm_email

  # V5 (Reliability)
  sqs_queue_name                = module.sqs.queue_name
  queue_max_message_age_seconds = var.queue_max_message_age_seconds
  payment_unavailable_threshold = var.payment_unavailable_threshold

  # V6 (Database HA)
  rds_replica_identifier             = module.rds.replica_identifier
  rds_replica_lag_threshold_seconds  = var.rds_replica_lag_threshold_seconds
  read_replica_unavailable_threshold = var.read_replica_unavailable_threshold

  tags = local.tags
}

module "ecs" {
  source = "../../modules/ecs"

  name               = local.name
  region             = var.region
  private_subnet_ids = module.vpc.private_subnet_ids
  security_group_id  = module.security_groups.ecs_sg_id
  container_port     = var.container_port
  image              = "${module.ecr.repository_url}:latest"
  cpu                = var.ecs_cpu
  memory             = var.ecs_memory
  desired_count      = var.ecs_desired_count
  execution_role_arn         = module.iam.ecs_task_execution_role_arn
  task_role_arn              = module.iam.ecs_task_role_arn
  product_target_group_arn   = module.alb.product_target_group_arn
  order_target_group_arn     = module.alb.order_target_group_arn
  payment_target_group_arn   = module.alb.payment_target_group_arn
  log_group_name             = module.cloudwatch.log_group_name
  rds_secret_arn             = module.rds.master_user_secret_arn
  db_host                    = module.rds.address
  db_port                    = module.rds.port
  db_name                    = module.rds.db_name
  product_db_name            = var.product_db_name
  order_db_name              = var.order_db_name
  payment_db_name            = var.payment_db_name
  payment_desired_count      = var.payment_desired_count
  redis_host                 = module.elasticache.address
  redis_port                 = module.elasticache.port
  cache_ttl_seconds          = var.cache_ttl_seconds
  cache_enabled              = var.cache_enabled

  sqs_queue_name           = module.sqs.queue_name
  worker_task_role_arn     = module.iam.ecs_worker_task_role_arn
  worker_security_group_id = module.security_groups.worker_sg_id
  worker_desired_count     = var.worker_desired_count

  # V5 (Reliability): timeouts, retry budgets, breaker/bulkhead limits, and
  # the fake payment gateway sidecar. See docs/adr/ADR-006-reliability.md.
  sqs_dlq_name                      = module.sqs.dlq_name
  sqs_visibility_timeout_seconds    = var.sqs_visibility_timeout_seconds
  payment_gateway_success_rate      = var.payment_gateway_success_rate
  payment_connect_timeout_seconds   = var.payment_connect_timeout_seconds
  payment_read_timeout_seconds      = var.payment_read_timeout_seconds
  payment_retry_attempts            = var.payment_retry_attempts
  circuit_breaker_failure_threshold = var.circuit_breaker_failure_threshold
  circuit_breaker_recovery_seconds  = var.circuit_breaker_recovery_seconds
  payment_bulkhead_max_concurrency  = var.payment_bulkhead_max_concurrency
  db_statement_timeout_seconds      = var.db_statement_timeout_seconds
  worker_handler_retry_attempts     = var.worker_handler_retry_attempts

  # V6 (Database HA): product reads may use the replica; the worker stays on
  # the primary. coalesce so a disabled replica still produces a valid
  # (empty) DB_READ_HOST string for the task definition.
  db_read_host            = module.rds.replica_address
  read_replica_enabled    = var.rds_read_replica_enabled
  db_pool_recycle_seconds = var.db_pool_recycle_seconds

  tags = local.tags
}

module "autoscaling" {
  source = "../../modules/autoscaling"

  name                       = local.name
  ecs_cluster_name           = module.ecs.cluster_name
  ecs_service_name           = module.ecs.service_name
  min_capacity               = var.ecs_min_capacity
  max_capacity               = var.ecs_max_capacity
  cpu_target_value           = var.ecs_cpu_target_value
  memory_target_value        = var.ecs_memory_target_value
  request_count_target_value = var.ecs_request_count_target_value
  alb_arn_suffix             = module.alb.arn_suffix
  target_group_arn_suffix    = module.alb.target_group_arn_suffix

  worker_ecs_service_name = module.ecs.worker_service_name
  sqs_queue_name          = module.sqs.queue_name
  sns_topic_arn           = module.cloudwatch.sns_topic_arn
  worker_min_capacity     = var.worker_min_capacity
  worker_max_capacity     = var.worker_max_capacity

  depends_on = [module.ecs]
}
