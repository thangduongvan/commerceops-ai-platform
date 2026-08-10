output "alb_dns_name" {
  description = "Public URL of the app: http://<this>/health"
  value       = module.alb.dns_name
}

output "ecr_repository_url" {
  value = module.ecr.repository_url
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "ecs_service_name" {
  value = module.ecs.service_name
}

output "rds_endpoint" {
  value = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  value = module.rds.master_user_secret_arn
}

output "github_actions_role_arn" {
  description = "Set as the AWS_ROLE_ARN GitHub Actions repo variable"
  value       = module.iam.github_actions_role_arn
}

output "cloudwatch_log_group" {
  value = module.cloudwatch.log_group_name
}

output "ecs_autoscaling_min_capacity" {
  value = module.autoscaling.min_capacity
}

output "ecs_autoscaling_max_capacity" {
  value = module.autoscaling.max_capacity
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint (host:port)"
  value       = "${module.elasticache.address}:${module.elasticache.port}"
}
