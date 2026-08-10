variable "region" {
  description = "AWS region for the state bucket and lock table"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Used to name the state bucket and lock table"
  type        = string
  default     = "commerceops"
}
