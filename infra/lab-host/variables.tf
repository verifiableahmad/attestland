variable "region" {
  type    = string
  default = "us-east-1" # the reaper's AWS role only terminates in this region
}

variable "az" {
  type        = string
  default     = "us-east-1a"
  description = "Zone with c5.metal on offer (aws ec2 describe-instance-type-offerings --location-type availability-zone)."
}

variable "vpc_cidr" {
  type    = string
  default = "10.30.0.0/16" # apart from the GPU pool (10.20.0.0/16) and the provisioning bridge (10.42.0.0/24)
}

variable "admin_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to SSH in."
}

variable "instance_type" {
  type        = string
  default     = "c5.metal" # 96 vCPU, 192 GiB, no hypervisor; cheapest x86 metal (~$4.08/h us-east-1, 2026-11-12)
  description = "Any *.metal x86 type; c7i.metal-24xl is a newer CPU for ~$0.20/h more."
  validation {
    condition     = can(regex("\\.metal", var.instance_type))
    error_message = "The lab host is bare metal: instance_type must be a *.metal type."
  }
}

variable "root_volume_gb" {
  type    = number
  default = 200
}

variable "owner" {
  type    = string
  default = "attestland"
}

variable "keep" {
  type        = bool
  default     = false
  description = "keep=true exempts the host from the 8-hour reaper (~$98/day for c5.metal)."
}

variable "ssh_user" {
  type    = string
  default = "al"
}

variable "ssh_public_key_path" {
  type    = string
  default = "~/.ssh/id_ed25519.pub"
}
