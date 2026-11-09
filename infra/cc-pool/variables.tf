variable "project_id" {
  type        = string
  description = "GCP project; the same one as the GPU pool."
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
  default = 250
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "A zone offering Confidential VM with H100."
  validation {
    condition     = contains(["us-central1-a", "us-east5-a", "europe-west4-c"], var.zone)
    error_message = "Confidential A3 High is documented only in us-central1-a, us-east5-a and europe-west4-c."
  }
}

variable "subnet_cidr" {
  type    = string
  default = "10.30.1.0/24"
}

variable "machine_type" {
  type    = string
  default = "a3-highgpu-1g" # 1x H100 80GB, Sapphire Rapids
}

variable "image" {
  type        = string
  default     = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
  description = "Ubuntu 22.04+ or COS 121+ are the images supported with confidential GPUs."
}

variable "boot_disk_type" {
  type    = string
  default = "pd-balanced"
}

variable "create_twin" {
  type        = bool
  default     = false
  description = "Create the non-CC twin only for the overhead benchmark (7.7)."
}

variable "admin_cidrs" {
  type = list(string)
}

variable "ssh_user" {
  type    = string
  default = "al"
}

variable "ssh_public_key_path" {
  type    = string
  default = "~/.ssh/id_ed25519.pub"
}

variable "owner" {
  type    = string
  default = "attestland"
}

variable "keep" {
  type        = bool
  default     = false
  description = "keep=true exempts the VMs from the 8-hour reaper."
}
