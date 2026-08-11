output "dns_name" {
  value = aws_lb.this.dns_name
}

output "arn_suffix" {
  description = "Used by CloudWatch alarms to reference this load balancer"
  value       = aws_lb.this.arn_suffix
}

output "product_target_group_arn" {
  value = aws_lb_target_group.product.arn
}

output "order_target_group_arn" {
  value = aws_lb_target_group.order.arn
}

output "payment_target_group_arn" {
  value = aws_lb_target_group.payment.arn
}

# Backward-compatible aliases used by autoscaling / cloudwatch (product TG =
# the flash-sale read path that V2–V3 scaling experiments target).
output "target_group_arn" {
  value = aws_lb_target_group.product.arn
}

output "target_group_arn_suffix" {
  description = "Used by CloudWatch / autoscaling for the product (read) target group"
  value       = aws_lb_target_group.product.arn_suffix
}

output "order_target_group_arn_suffix" {
  value = aws_lb_target_group.order.arn_suffix
}
