variable "name" {
  description = "Name prefix for IAM resources"
  type        = string
}

variable "region" {
  description = "AWS region (used to build resource ARNs)"
  type        = string
}

variable "rds_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the RDS managed master password"
  type        = string
}

variable "app_assets_bucket_arn" {
  description = "ARN of the S3 bucket the app's ECS task role may read/write"
  type        = string
}

variable "ecr_repository_arn" {
  description = "ARN of the ECR repository GitHub Actions may push images to"
  type        = string
}

variable "ecs_cluster_name" {
  description = "Name of the ECS cluster (used to scope the GitHub Actions deploy role, created by the ecs module)"
  type        = string
}

variable "ecs_service_name" {
  description = "Name of the ECS service (used to scope the GitHub Actions deploy role, created by the ecs module)"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo allowed to assume the CI/CD role, in \"owner/repo\" form"
  type        = string
}

variable "github_branch" {
  description = "Branch allowed to assume the CI/CD role via OIDC"
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "Whether to create the GitHub OIDC provider. Set to false if one already exists in the account (an AWS account can only have one provider per URL)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
