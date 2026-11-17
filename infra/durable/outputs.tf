output "bucket_prefix" {
  value = local.bucket_prefix
}

output "region" {
  value = var.region
}

# Read by the Makefile for infra/gpu-pool's backend (-backend-config=bucket=...).
output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
