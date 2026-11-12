variable "subscription_id" {
  type        = string
  description = "Azure subscription that holds the confidential pool."
}

variable "location" {
  type        = string
  default     = "eastus2"
  description = "Must offer NCC40ads_H100_v5 to the subscription (az vm list-skus --all); eastus2, centralus and westeurope do."
}

variable "zone" {
  type        = string
  default     = "1"
  description = "Availability zone offering the size; NCC40ads_H100_v5 is in eastus2 zone 1 only."
}

variable "maa_url" {
  type        = string
  default     = "https://sharedeus2.eus2.attest.azure.net"
  description = "Microsoft Azure Attestation instance the issuer uses; the shared one in the pool's region."
}

variable "cc_vm_size" {
  type    = string
  default = "Standard_NCC40ads_H100_v5" # AMD SEV-SNP + 1x H100 NVL, CC on
}

variable "twin_vm_size" {
  type    = string
  default = "Standard_NC40ads_H100_v5" # same GPU, no SEV-SNP, no CC
}

variable "create_twin" {
  type        = bool
  default     = false
  description = "Create the non-CC twin only for the overhead benchmark (7.7)."
}

variable "admin_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach SSH."
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

variable "budget_usd" {
  type    = number
  default = 250
}

variable "budget_start_date" {
  type        = string
  default     = "2026-11-01T00:00:00Z"
  description = "First day of a month, RFC3339."
}

variable "budget_contact_emails" {
  type        = list(string)
  default     = []
  description = "Budget alerts (50/80/100%) go here. Empty skips the budget."
}
