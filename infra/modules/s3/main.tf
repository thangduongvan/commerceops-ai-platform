data "aws_caller_identity" "current" {}

### ALB access logs bucket
### V1 wires this in purely for the "Logs" deliverable / S3 learning goal — the ALB
### module points its access_logs block at this bucket.

resource "aws_s3_bucket" "alb_logs" {
  bucket = "${var.name}-alb-logs-${data.aws_caller_identity.current.account_id}"

  tags = merge(var.tags, { Name = "${var.name}-alb-logs" })
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket                  = aws_s3_bucket.alb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-old-access-logs"
    status = "Enabled"
    filter {}

    expiration {
      days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowELBLogDelivery"
        Effect    = "Allow"
        Principal = { Service = "logdelivery.elasticloadbalancing.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.alb_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" }
        }
      }
    ]
  })
}

### General-purpose app storage bucket
### Not wired into the app yet in V1 — reserved for future use (e.g. product images).
### Provisioned now so the ECS task role's S3 permissions can be scoped to a real
### resource instead of "*".

resource "aws_s3_bucket" "app_assets" {
  bucket = "${var.name}-app-assets-${data.aws_caller_identity.current.account_id}"

  tags = merge(var.tags, { Name = "${var.name}-app-assets" })
}

resource "aws_s3_bucket_public_access_block" "app_assets" {
  bucket                  = aws_s3_bucket.app_assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "app_assets" {
  bucket = aws_s3_bucket.app_assets.id

  versioning_configuration {
    status = "Enabled"
  }
}
