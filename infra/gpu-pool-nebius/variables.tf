variable "project_id" {
  type        = string
  description = "Nebius project (project-...), in the region of the GPU platform."
}

variable "tenant_id" {
  type        = string
  description = "Tenant (tenant-...) that owns the project; its editors group gets the S3 service account."
}

variable "cli_profile" {
  type    = string
  default = "default"
}

variable "region" {
  type        = string
  default     = "eu-north1"
  description = "The project's region; L40S and H100 are only in eu-north1."
}

variable "admin_cidrs" {
  type        = list(string)
  description = "Addresses allowed to reach ssh, Grafana and the k8s API."
}

variable "gpu_nodes" {
  type    = number
  default = 2
}

variable "preemptible_gpu_nodes" {
  type        = number
  default     = 0
  description = "How many of the GPU nodes (the last ones) are preemptible. Not for timed benchmarks."
}

variable "gpu_platform" {
  type    = string
  default = "gpu-l40s-a" # NVIDIA L40S 48 GB, Intel Ice Lake
}

variable "gpu_preset" {
  type    = string
  default = "1gpu-8vcpu-32gb" # the CPU and memory of g6.2xlarge
}

variable "gpu_disk_gib" {
  type    = number
  default = 400 # boot + /scratch
}

variable "control_platform" {
  type    = string
  default = "cpu-d3" # AMD Genoa
}

variable "control_preset" {
  type    = string
  default = "4vcpu-16gb"
}

variable "gpu_image_family" {
  type    = string
  default = "ubuntu24.04-driverless"
}

variable "cpu_image_family" {
  type    = string
  default = "ubuntu24.04-driverless"
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
  description = "Exempt the VMs from the 8-hour reaper."
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
  description = "Bucket name prefix; empty means attestland-<project id>."
}
