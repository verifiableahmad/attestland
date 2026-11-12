variable "region" {
  type    = string
  default = "us-east-1"
}

variable "azs" {
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1f"]
  description = "Zones that get a subnet; all offer g6.2xlarge (aws ec2 describe-instance-type-offerings)."
}

variable "az" {
  type        = string
  default     = "us-east-1a"
  description = "Zone for every pool VM (NCCL across zones is slower and billed). Change it when a zone has no g6 capacity."
  validation {
    condition     = contains(var.azs, var.az)
    error_message = "az must be one of azs."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "admin_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach SSH, Grafana and the k8s API."
}

variable "gpu_nodes" {
  type    = number
  default = 4
}

variable "gpu_instance_type" {
  type    = string
  default = "g6.2xlarge" # 1x NVIDIA L4, 8 vCPU, 32 GiB, 450 GB NVMe, up to 10 Gbps
}

variable "control_instance_type" {
  type    = string
  default = "m7i.xlarge" # 4 vCPU, 16 GiB
}

variable "spot" {
  type        = bool
  default     = false
  description = "Spot for development once the account has G spot quota (it is 0 now); false for every timed benchmark."
}

variable "cluster_placement" {
  type        = bool
  default     = true
  description = "Place GPU VMs in a cluster placement group (on-demand runs only) so they sit close (ADR 007)."
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

variable "budget_usd" {
  type    = number
  default = 300
}

variable "budget_contact_emails" {
  type        = list(string)
  default     = []
  description = "Budget alerts (50/80/100%) go here. Empty skips the budget."
}

variable "bucket_prefix" {
  type        = string
  default     = ""
  description = "S3 bucket names are global; buckets are <prefix>-<name>. Empty: attestland-<account id>."
}

variable "bucket_force_destroy" {
  type        = bool
  default     = false
  description = "Let tofu destroy delete buckets that still hold objects (checkpoints, datasets, models, receipts)."
}
