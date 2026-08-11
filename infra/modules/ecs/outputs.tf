output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "cluster_id" {
  value = aws_ecs_cluster.this.id
}

output "service_name" {
  value = aws_ecs_service.app.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}
