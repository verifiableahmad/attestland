# Durable tier (spec 5.1): Nebius Object Storage buckets, versioned, with the same
# names as on AWS. Nebius VMs have no instance role, so the nodes use the S3 key
# of a service account in the tenant's editors group; Ansible reads it from the
# tofu outputs (never from the inventory, which is tracked).
locals {
  bucket_prefix = var.bucket_prefix != "" ? var.bucket_prefix : "attestland-${trimprefix(var.project_id, "project-")}"
  buckets       = ["datasets", "checkpoints", "models", "receipts"]
}

resource "nebius_storage_v1_bucket" "durable" {
  for_each          = toset(local.buckets)
  parent_id         = var.project_id
  name              = "${local.bucket_prefix}-${each.key}"
  versioning_policy = "ENABLED"
  # Versioning keeps every overwritten checkpoint; do not let old versions pile up.
  lifecycle_configuration = {
    rules = [{
      id                                = "expire-noncurrent"
      status                            = "ENABLED"
      filter                            = { prefix = "" }
      noncurrent_version_expiration     = { noncurrent_days = 14 }
      abort_incomplete_multipart_upload = { days_after_initiation = 2 }
    }]
  }
}

resource "nebius_iam_v1_service_account" "s3" {
  parent_id = var.project_id
  name      = "al-gpu-s3"
}

data "nebius_iam_v1_group" "editors" {
  name      = "editors"
  parent_id = var.tenant_id
}

resource "nebius_iam_v1_group_membership" "s3" {
  parent_id = data.nebius_iam_v1_group.editors.id
  member_id = nebius_iam_v1_service_account.s3.id
}

resource "nebius_iam_v2_access_key" "s3" {
  parent_id            = var.project_id
  name                 = "al-gpu-s3"
  account              = { service_account = { id = nebius_iam_v1_service_account.s3.id } }
  secret_delivery_mode = "INLINE" # the secret lands in the tofu state, read by Ansible
}
