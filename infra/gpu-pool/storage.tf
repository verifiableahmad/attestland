# Durable tier (spec 5.1): S3 buckets, versioned, read and written by the nodes
# through their instance roles (ADR 010; no long-lived keys). The buckets live
# in infra/durable (`make durable-up`), so `make down` leaves them and their
# data alone; this module only looks them up and grants access.
locals {
  bucket_prefix = var.bucket_prefix != "" ? var.bucket_prefix : "attestland-${data.aws_caller_identity.current.account_id}"
  buckets       = ["datasets", "checkpoints", "models", "receipts"]
}

# Fails at plan time if `make durable-up` has not run.
data "aws_s3_bucket" "durable" {
  for_each = toset(local.buckets)
  bucket   = "${local.bucket_prefix}-${each.key}"
}

# Before the split these were resources of this module: forget them, never
# destroy them (infra/durable imports what is left).
removed {
  from = aws_s3_bucket.durable
  lifecycle { destroy = false }
}

removed {
  from = aws_s3_bucket_public_access_block.durable
  lifecycle { destroy = false }
}

removed {
  from = aws_s3_bucket_versioning.durable
  lifecycle { destroy = false }
}

removed {
  from = aws_s3_bucket_lifecycle_configuration.durable
  lifecycle { destroy = false }
}

# GPU nodes, and every pod on them through IMDS (vLLM's weight fetch): what jobs
# read and write, no deletes, no receipts (SEC-2).
resource "aws_iam_role_policy" "gpu_s3" {
  name = "durable-buckets"
  role = aws_iam_role.gpu.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [for b in ["datasets", "checkpoints", "models"] : data.aws_s3_bucket.durable[b].arn]
      },
      {
        Effect   = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = [for b in ["datasets", "checkpoints", "models"] : "${data.aws_s3_bucket.durable[b].arn}/*"]
      },
    ]
  })
}

# Control node: all four buckets (model exports, receipts uploads); receipts are
# append-only for everyone.
resource "aws_iam_role_policy" "control_s3" {
  name = "durable-buckets"
  role = aws_iam_role.control.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = [for b in data.aws_s3_bucket.durable : b.arn] },
      {
        Effect   = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = [for b in data.aws_s3_bucket.durable : "${b.arn}/*"]
      },
      { Effect = "Deny", Action = ["s3:DeleteObject", "s3:DeleteObjectVersion"], Resource = "${data.aws_s3_bucket.durable["receipts"].arn}/*" },
    ]
  })
}
