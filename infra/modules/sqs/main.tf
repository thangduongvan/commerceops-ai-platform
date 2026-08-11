### V4 note: one queue for every order event type (OrderCreated/OrderPaid/
### OrderPaymentFailed/OrderCancelled), fanned out to all four consumers
### (notification/email/analytics/search) by app/worker.py — see the
### architecture diagram in docs/adr/ADR-005-async-processing.md. No
### per-event-type routing yet; that's deferred to V8 (Event-Driven
### Architecture / EventBridge) once there's an actual reason for it.

resource "aws_sqs_queue" "dlq" {
  name = "${var.name}-order-events-dlq"

  # Failed messages land here after max_receive_count delivery attempts.
  # 14 days (SQS's maximum) so there's time to notice and redrive them
  # before they're gone for good — see the dlq_messages_present alarm in
  # infra/modules/cloudwatch.
  message_retention_seconds = 1209600

  tags = merge(var.tags, { Name = "${var.name}-order-events-dlq" })
}

resource "aws_sqs_queue" "order_events" {
  name = "${var.name}-order-events"

  visibility_timeout_seconds = var.visibility_timeout_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = merge(var.tags, { Name = "${var.name}-order-events" })
}
