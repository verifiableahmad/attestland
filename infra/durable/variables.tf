variable "region" {
  type    = string
  default = "us-east-1"
}

variable "owner" {
  type    = string
  default = "attestland"
}

variable "bucket_prefix" {
  type        = string
  default     = ""
  description = "S3 bucket names are global; buckets are <prefix>-<name>. Empty: attestland-<account id>. infra/gpu-pool must use the same."
}

variable "import_buckets" {
  type        = list(string)
  default     = []
  description = "Buckets that already exist (made by infra/gpu-pool before the split) to adopt instead of create."
}
