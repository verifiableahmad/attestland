# Reaper identities (ADR 010): what .github/workflows/reaper.yml signs in as.
# No stored secrets: GitHub's OIDC token is exchanged for an AWS role and an
# Azure app, each trusted only for workflows on one branch of one repository and
# allowed only to list and delete VMs. The repository variables the workflow
# reads are written here too. Apply once per account (make reaper-up).
terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { project = "attestland", owner = var.owner }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}

provider "azuread" {
  tenant_id = var.tenant_id
}

# Token from GITHUB_TOKEN (make reaper-up passes `gh auth token`).
provider "github" {
  owner = split("/", var.github_repo)[0]
}

# Numeric ids for the immutable `sub` format (owner@id/repo@id), which this
# repository's OIDC settings use (gh api repos/<repo>/actions/oidc/customization/sub).
data "github_repository" "this" {
  full_name = var.github_repo
}

data "github_user" "owner" {
  username = split("/", var.github_repo)[0]
}

locals {
  github_issuer = "https://token.actions.githubusercontent.com"
  # The `sub` claim of a job on that branch with no environment.
  github_subject = format("repo:%s@%s/%s@%s:ref:refs/heads/%s",
    data.github_user.owner.login, data.github_user.owner.id,
    data.github_repository.this.name, data.github_repository.this.repo_id,
    var.github_branch)
}

# --- AWS: OIDC provider and role --------------------------------------------
# One provider per URL per account; set create_oidc_provider = false if it exists.
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = local.github_issuer
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = local.github_issuer
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

data "aws_iam_policy_document" "reaper_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_subject]
    }
  }
}

# Terminate only project instances, never keep=true ones, even if reap.sh errs.
data "aws_iam_policy_document" "reaper" {
  statement {
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  statement {
    actions   = ["ec2:TerminateInstances"]
    resources = ["arn:aws:ec2:${var.aws_region}:*:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = ["attestland"]
    }
  }
  statement {
    effect    = "Deny"
    actions   = ["ec2:TerminateInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/keep"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role" "reaper" {
  name                 = "al-reaper"
  assume_role_policy   = data.aws_iam_policy_document.reaper_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "reaper" {
  name   = "reap-instances"
  role   = aws_iam_role.reaper.id
  policy = data.aws_iam_policy_document.reaper.json
}

# --- Azure: app registration, federated credential, VM-delete role ----------
resource "azuread_application" "reaper" {
  display_name = "al-reaper"
}

resource "azuread_service_principal" "reaper" {
  client_id = azuread_application.reaper.client_id
}

resource "azuread_application_federated_identity_credential" "reaper" {
  application_id = azuread_application.reaper.id
  display_name   = "github-${replace(var.github_repo, "/", "-")}-${var.github_branch}"
  issuer         = local.github_issuer
  subject        = local.github_subject
  audiences      = ["api://AzureADTokenExchange"]
}

# reap.sh needs `az vm list` and `az vm delete`, nothing else.
resource "azurerm_role_definition" "reaper" {
  name              = "al-reaper"
  scope             = "/subscriptions/${var.subscription_id}"
  description       = "List and delete VMs (attestland reaper)."
  assignable_scopes = ["/subscriptions/${var.subscription_id}"]
  permissions {
    actions = [
      "Microsoft.Compute/virtualMachines/read",
      "Microsoft.Compute/virtualMachines/delete",
    ]
  }
}

resource "azurerm_role_assignment" "reaper" {
  scope              = "/subscriptions/${var.subscription_id}"
  role_definition_id = azurerm_role_definition.reaper.role_definition_resource_id
  principal_id       = azuread_service_principal.reaper.object_id
  principal_type     = "ServicePrincipal"
}

# --- GitHub: the variables reaper.yml reads ---------------------------------
locals {
  repo_variables = {
    AWS_REGION            = var.aws_region
    AWS_REAPER_ROLE_ARN   = aws_iam_role.reaper.arn
    AZURE_CLIENT_ID       = azuread_application.reaper.client_id
    AZURE_TENANT_ID       = var.tenant_id
    AZURE_SUBSCRIPTION_ID = var.subscription_id
  }
}

resource "github_actions_variable" "reaper" {
  for_each      = local.repo_variables
  repository    = split("/", var.github_repo)[1]
  variable_name = each.key
  value         = each.value
}
