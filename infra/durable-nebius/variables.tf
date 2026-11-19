variable "project_id" {
  type        = string
  description = "Nebius project (project-...), the same as infra/gpu-pool-nebius."
}

variable "tenant_id" {
  type        = string
  description = "Tenant (tenant-...) that owns the project; its editors group gets the S3 service account."
}

variable "cli_profile" {
  type    = string
  default = "default"
}

variable "bucket_prefix" {
  type        = string
  default     = ""
  description = "Bucket name prefix; empty means attestland-<project id>. infra/gpu-pool-nebius derives the same default."
}
