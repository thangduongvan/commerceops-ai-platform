locals {
  name = "${var.project_name}-${var.environment}"

  # ECS cluster/service names, computed the same way the ecs module names them
  # internally. Passed as plain strings to the iam and cloudwatch modules so
  # they can build ARNs / alarm dimensions without depending on the ecs module
  # itself (avoids a module dependency cycle: ecs depends on iam and cloudwatch).
  ecs_cluster_name = "${local.name}-cluster"
  ecs_service_name = "${local.name}-app"

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
  tags               = local.tags
}

module "iam" {
  source = "../../modules/iam"

  name                  = local.name
  region                = var.region
  rds_secret_arn        = module.rds.master_user_secret_arn
  app_assets_bucket_arn = module.s3.app_assets_bucket_arn
  ecr_repository_arn    = module.ecr.repository_arn
  ecs_cluster_name      = local.ecs_cluster_name
  ecs_service_name      = local.ecs_service_name
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
  alarm_email             = var.alarm_email
  tags                    = local.tags
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
  execution_role_arn = module.iam.ecs_task_execution_role_arn
  task_role_arn      = module.iam.ecs_task_role_arn
  target_group_arn   = module.alb.target_group_arn
  log_group_name     = module.cloudwatch.log_group_name
  rds_secret_arn     = module.rds.master_user_secret_arn
  db_host            = module.rds.address
  db_port            = module.rds.port
  db_name            = module.rds.db_name
  tags               = local.tags
}
