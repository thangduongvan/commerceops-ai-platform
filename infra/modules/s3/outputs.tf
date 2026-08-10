output "alb_logs_bucket_id" {
  value = aws_s3_bucket.alb_logs.id
}

output "app_assets_bucket_id" {
  value = aws_s3_bucket.app_assets.id
}

output "app_assets_bucket_arn" {
  value = aws_s3_bucket.app_assets.arn
}
