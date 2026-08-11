variable "name" {
  description = "Name prefix for SQS resources"
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a received message is hidden from other consumers before it's considered failed and becomes visible again (SQS's own retry mechanism). Must comfortably exceed the worker's typical handler runtime — see ADR-005."
  type        = number
  default     = 30
}

variable "max_receive_count" {
  description = "Delivery attempts before a message moves to the DLQ"
  type        = number
  default     = 5
}

variable "tags" {
  type    = map(string)
  default = {}
}
