output "dns_name" {
  value = aws_lb.this.dns_name
}

output "arn_suffix" {
  description = "Used by CloudWatch alarms to reference this load balancer"
  value       = aws_lb.this.arn_suffix
}

output "target_group_arn" {
  value = aws_lb_target_group.app.arn
}

output "target_group_arn_suffix" {
  description = "Used by CloudWatch alarms to reference this target group"
  value       = aws_lb_target_group.app.arn_suffix
}
