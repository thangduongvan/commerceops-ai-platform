### One-time stack: creates the S3 bucket + DynamoDB table that
### `infra/environments/*` use as their remote Terraform state backend.
###
### This stack intentionally keeps its OWN state local (see backend note
### in README) — you can't store this stack's state in the bucket it creates.
### Run this once per AWS account/region; environments/dev then points at
### its outputs in backend.tf.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "tf_state" {
  bucket = "${var.project_name}-tf-state-${data.aws_caller_identity.current.account_id}"

  tags = {
    Project   = var.project_name
    Purpose   = "terraform-state"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tf_lock" {
  name         = "${var.project_name}-tf-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Project   = var.project_name
    Purpose   = "terraform-state-lock"
    ManagedBy = "terraform-bootstrap"
  }
}
