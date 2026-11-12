variable "github_repo" {
  type        = string
  default     = "verifiableahmad/attestland"
  description = "owner/name of the repository whose reaper workflow may sign in."
}

variable "github_branch" {
  type        = string
  default     = "main"
  description = "Only workflow runs on this branch get credentials (scheduled runs use the default branch)."
}

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "Region the reaper sweeps; the GPU pool's region."
}

variable "create_oidc_provider" {
  type        = bool
  default     = true
  description = "false if the account already has the token.actions.githubusercontent.com provider."
}

variable "subscription_id" {
  type        = string
  description = "Azure subscription the reaper sweeps; the confidential pool's."
}

variable "tenant_id" {
  type        = string
  description = "Entra tenant that owns the subscription (may differ from your login's home tenant)."
}

variable "owner" {
  type    = string
  default = "attestland"
}
