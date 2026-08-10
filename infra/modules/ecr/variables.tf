variable "name" {
  description = "Name of the ECR repository"
  type        = string
}

variable "image_count_to_keep" {
  description = "Number of most recent images to retain before expiring older ones"
  type        = number
  default     = 10
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
