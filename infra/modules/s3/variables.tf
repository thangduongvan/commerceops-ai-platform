variable "name" {
  description = "Name prefix for S3 buckets (bucket names are suffixed with the account ID for global uniqueness)"
  type        = string
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
