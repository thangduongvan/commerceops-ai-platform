variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "commerceops"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "azs" {
  description = "Two availability zones to spread subnets across"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.0.0/24", "10.0.1.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.10.0/24", "10.0.11.0/24"]
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "db_port" {
  type    = number
  default = 5432
}

variable "db_name" {
  type    = string
  default = "commerceops"
}

variable "db_username" {
  type    = string
  default = "commerceops"
}

variable "rds_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "ecs_cpu" {
  type    = number
  default = 256
}

variable "ecs_memory" {
  type    = number
  default = 512
}

variable "ecs_desired_count" {
  type    = number
  default = 1
}

variable "github_repo" {
  description = "GitHub repo allowed to deploy via OIDC, in \"owner/repo\" form"
  type        = string
  default     = "thangduongvan/commerceops-ai-platform"
}

variable "github_branch" {
  type    = string
  default = "main"
}

variable "alarm_email" {
  description = "Email to notify on CloudWatch alarms. Leave empty to skip the subscription."
  type        = string
  default     = ""
}
