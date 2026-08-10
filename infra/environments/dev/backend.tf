# Remote state: S3 bucket + DynamoDB lock table created once by `infra/bootstrap`.
#
# Terraform does not allow variables here, so fill in the two account-specific
# values (bucket, dynamodb_table) from `terraform output` in infra/bootstrap
# — see docs/deployment.md step 2. Alternatively, leave the placeholders and
# pass them at init time instead:
#   terraform init -backend-config="bucket=<state_bucket>" -backend-config="dynamodb_table=<lock_table>"
terraform {
  backend "s3" {
    bucket         = "REPLACE_WITH_BOOTSTRAP_STATE_BUCKET"
    key            = "commerceops/dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "REPLACE_WITH_BOOTSTRAP_LOCK_TABLE"
    encrypt        = true
  }
}
