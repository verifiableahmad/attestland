# Durable tier (spec 5.1): Cloud Storage buckets, versioned, read and written through the
# S3 interoperability API with an HMAC key (ADR 008). Nodes also have the service account,
# so gcloud and native clients work without the key.
locals {
  bucket_prefix = var.bucket_prefix != "" ? var.bucket_prefix : var.project_id
  buckets       = ["datasets", "checkpoints", "models", "receipts"]
}

resource "google_storage_bucket" "durable" {
  for_each                    = toset(local.buckets)
  name                        = "${local.bucket_prefix}-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.bucket_force_destroy
  labels                      = local.labels
  versioning { enabled = true }

  # Versioning keeps every overwritten checkpoint; do not let old versions pile up.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 14
      with_state                 = "ARCHIVED"
    }
    action { type = "Delete" }
  }
}

resource "google_storage_bucket_iam_member" "nodes" {
  for_each = google_storage_bucket.durable
  bucket   = each.value.name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_storage_hmac_key" "nodes" {
  service_account_email = google_service_account.nodes.email
}
