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

# V5 (Reliability): without this, AWS's own DLQ redrive
# (`aws sqs start-message-move-task`, or the console's "Start DLQ redrive")
# refuses to move messages back to the source queue. V4 documented redrive as
# the recovery path but never authorized it — so the documented runbook would
# have failed the first time anyone actually needed it. `python -m app.dlq
# redrive` doesn't need this (it's an ordinary send to the main queue), but the
# AWS-native path is the one an operator reaches for first.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.order_events.arn]
  })
}

resource "aws_sqs_queue" "order_events" {
  name = "${var.name}-order-events"

  visibility_timeout_seconds = var.visibility_timeout_seconds

  # V5: long polling at the queue level, not just in the client's
  # receive_message call. Any consumer that forgets WaitTimeSeconds (the DLQ
  # tooling, an ad-hoc `aws sqs receive-message`) now still waits for messages
  # to arrive instead of returning empty immediately and being retried in a
  # tight, billable loop.
  receive_wait_time_seconds = var.receive_wait_time_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = merge(var.tags, { Name = "${var.name}-order-events" })
}
