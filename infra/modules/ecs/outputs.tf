output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "cluster_id" {
  value = aws_ecs_cluster.this.id
}

output "product_service_name" {
  value = aws_ecs_service.product.name
}

output "order_service_name" {
  value = aws_ecs_service.order.name
}

output "payment_service_name" {
  value = aws_ecs_service.payment.name
}

# Primary autoscaling / alarm target: Product (read-heavy flash-sale path).
output "service_name" {
  value = aws_ecs_service.product.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "service_connect_namespace_arn" {
  value = aws_service_discovery_http_namespace.this.arn
}
