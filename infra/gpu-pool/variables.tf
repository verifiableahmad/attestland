variable "project_id" {
  type        = string
  description = "GCP project that holds the GPU pool."
}

variable "project_number" {
  type        = string
  default     = ""
  description = "Numeric project id; only needed for the billing budget."
}

variable "billing_account_id" {
  type        = string
  default     = ""
  description = "Billing account for budget alerts (50/80/100%). Empty skips the budget."
}

variable "budget_usd" {
  type    = number
  default = 300
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "subnet_cidr" {
  type    = string
  default = "10.20.0.0/24"
}

variable "admin_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach SSH, Grafana and the k8s API."
}

variable "gpu_nodes" {
  type    = number
  default = 4
}

variable "gpu_machine_type" {
  type    = string
  default = "g2-standard-8" # 1x NVIDIA L4
}

variable "control_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "gpu_image" {
  type        = string
  default     = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
  description = "Use the Packer image family (al-gpu-ubuntu-2204) once built."
}

variable "cpu_image" {
  type    = string
  default = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
}

variable "spot" {
  type        = bool
  default     = true
  description = "Spot for development; set false for every timed benchmark."
}

variable "compact_placement" {
  type        = bool
  default     = true
  description = "Place GPU VMs in a compact placement policy (on-demand runs only) so they sit close and expose their topology (ADR 007)."
}

variable "phase" {
  type    = number
  default = 2
}

variable "owner" {
  type    = string
  default = "attestland"
}

variable "keep" {
  type        = bool
  default     = false
  description = "keep=true exempts instances from the 8-hour reaper."
}

variable "ssh_user" {
  type    = string
  default = "al"
}

variable "ssh_public_key_path" {
  type    = string
  default = "~/.ssh/id_ed25519.pub"
}

variable "bucket_prefix" {
  type        = string
  default     = ""
  description = "Cloud Storage bucket names are global; buckets are <prefix>-<name>. Empty: the project id."
}

variable "bucket_force_destroy" {
  type        = bool
  default     = false
  description = "Let tofu destroy delete buckets that still hold objects (checkpoints, datasets, models, receipts)."
}
