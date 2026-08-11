output "queue_name" {
  value = aws_sqs_queue.order_events.name
}

output "queue_arn" {
  value = aws_sqs_queue.order_events.arn
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}
