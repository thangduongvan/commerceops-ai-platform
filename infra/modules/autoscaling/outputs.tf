output "min_capacity" {
  value = aws_appautoscaling_target.ecs.min_capacity
}

output "max_capacity" {
  value = aws_appautoscaling_target.ecs.max_capacity
}

output "cpu_policy_arn" {
  value = aws_appautoscaling_policy.cpu.arn
}

output "memory_policy_arn" {
  value = aws_appautoscaling_policy.memory.arn
}

output "request_count_policy_arn" {
  value = aws_appautoscaling_policy.request_count.arn
}
