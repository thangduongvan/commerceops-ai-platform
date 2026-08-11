variable "name" {
  description = "Name prefix for SQS resources"
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a received message is hidden from other consumers before it's considered failed and becomes visible again (SQS's own retry mechanism). V5 raised the default from 30s to 60s: the worker's in-process retry ladder has to fit inside this window, or the message is redelivered to a second worker while the first is still retrying it — see ADR-006."
  type        = number
  default     = 60
}

variable "receive_wait_time_seconds" {
  description = "V5: queue-level long polling, so a consumer that omits WaitTimeSeconds still waits instead of returning empty immediately in a tight loop"
  type        = number
  default     = 20
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
