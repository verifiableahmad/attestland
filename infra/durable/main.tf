# Durable tier (spec 5.1, ADR 010; D-2 of the 2026-11-16 review): the S3 buckets
# and the bucket that holds the GPU pool's OpenTofu state. Its own root module
# and state, so `make down` (infra/gpu-pool) neither deletes the data nor stops
# on BucketNotEmpty. Apply once per account: `make durable-up`. Nodes reach the
# buckets through their instance roles (infra/gpu-pool); there are no keys.
terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "attestland", owner = var.owner, tier = "durable" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  bucket_prefix = var.bucket_prefix != "" ? var.bucket_prefix : "attestland-${data.aws_caller_identity.current.account_id}"
  buckets       = ["datasets", "checkpoints", "models", "receipts"]
}

# Buckets made by infra/gpu-pool before the split: adopted, not recreated
# (import_buckets in terraform.tfvars, e.g. ["datasets"]).
import {
  for_each = toset(var.import_buckets)
  to       = aws_s3_bucket.durable[each.key]
  id       = "${local.bucket_prefix}-${each.key}"
}

resource "aws_s3_bucket" "durable" {
  for_each = toset(local.buckets)
  bucket   = "${local.bucket_prefix}-${each.key}"
  # Never deleted by OpenTofu, not even when empty: remove a bucket by hand.
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

# The GPU pool's state (backend "s3" with use_lockfile; OPS-2): versioned, so
# every apply's state stays recoverable.
resource "aws_s3_bucket" "state" {
  bucket = "${local.bucket_prefix}-tfstate"
  lifecycle { prevent_destroy = true }
}

locals {
  all_buckets = merge(aws_s3_bucket.durable, { tfstate = aws_s3_bucket.state })
}

resource "aws_s3_bucket_public_access_block" "durable" {
  for_each                = local.all_buckets
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "durable" {
  for_each = local.all_buckets
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}

# Versioning keeps every overwritten checkpoint; do not let old versions pile up.
resource "aws_s3_bucket_lifecycle_configuration" "durable" {
  for_each = local.all_buckets
  bucket   = each.value.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = each.key == "tfstate" ? 90 : 14 }
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
  depends_on = [aws_s3_bucket_versioning.durable]
}
