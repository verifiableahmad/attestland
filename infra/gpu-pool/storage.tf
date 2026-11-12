# Durable tier (spec 5.1): S3 buckets, versioned, read and written by the nodes
# through their instance role (ADR 010; no long-lived keys).
locals {
  bucket_prefix = var.bucket_prefix != "" ? var.bucket_prefix : "attestland-${data.aws_caller_identity.current.account_id}"
  buckets       = ["datasets", "checkpoints", "models", "receipts"]
}

resource "aws_s3_bucket" "durable" {
  for_each      = toset(local.buckets)
  bucket        = "${local.bucket_prefix}-${each.key}"
  force_destroy = var.bucket_force_destroy
}

resource "aws_s3_bucket_public_access_block" "durable" {
  for_each                = aws_s3_bucket.durable
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "durable" {
  for_each = aws_s3_bucket.durable
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}

# Versioning keeps every overwritten checkpoint; do not let old versions pile up.
resource "aws_s3_bucket_lifecycle_configuration" "durable" {
  for_each = aws_s3_bucket.durable
  bucket   = each.value.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 14 }
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
  depends_on = [aws_s3_bucket_versioning.durable]
}

resource "aws_iam_role_policy" "durable" {
  name = "durable-buckets"
  role = aws_iam_role.nodes.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = [for b in aws_s3_bucket.durable : b.arn] },
      {
        Effect   = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = [for b in aws_s3_bucket.durable : "${b.arn}/*"]
      },
    ]
  })
}
