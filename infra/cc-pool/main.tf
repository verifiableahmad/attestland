# Confidential pool (phase 5, ADR 010): one Azure Standard_NCC40ads_H100_v5
# confidential VM (AMD SEV-SNP + 1x H100 NVL with NVIDIA CC on) and, for the
# overhead benchmark, its non-confidential twin Standard_NC40ads_H100_v5. Create
# the twin only when needed. Both are on-demand.
terraform {
  required_version = ">= 1.8"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

locals {
  tags = {
    project = "attestland"
    phase   = "5"
    owner   = var.owner
    keep    = var.keep ? "true" : "false"
  }
  ssh_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

resource "azurerm_resource_group" "cc" {
  name     = "al-cc-${var.location}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "cc" {
  name                = "al-cc"
  address_space       = ["10.30.0.0/16"]
  location            = azurerm_resource_group.cc.location
  resource_group_name = azurerm_resource_group.cc.name
  tags                = local.tags
}

resource "azurerm_subnet" "cc" {
  name                 = "vms"
  resource_group_name  = azurerm_resource_group.cc.name
  virtual_network_name = azurerm_virtual_network.cc.name
  address_prefixes     = ["10.30.1.0/24"]
}

resource "azurerm_network_security_group" "cc" {
  name                = "al-cc"
  location            = azurerm_resource_group.cc.location
  resource_group_name = azurerm_resource_group.cc.name
  tags                = local.tags

  security_rule {
    name                       = "ssh-admin"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefixes    = var.admin_cidrs
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "cc" {
  subnet_id                 = azurerm_subnet.cc.id
  network_security_group_id = azurerm_network_security_group.cc.id
}

module "confidential" {
  source              = "./vm"
  name                = "al-cc-h100"
  size                = var.cc_vm_size
  confidential        = true
  zone                = var.zone
  resource_group_name = azurerm_resource_group.cc.name
  location            = azurerm_resource_group.cc.location
  subnet_id           = azurerm_subnet.cc.id
  admin_username      = var.ssh_user
  ssh_public_key      = local.ssh_key
  tags                = merge(local.tags, { role = "cc" })
}

module "twin" {
  count               = var.create_twin ? 1 : 0
  source              = "./vm"
  name                = "al-noncc-h100"
  size                = var.twin_vm_size
  confidential        = false
  zone                = var.zone
  resource_group_name = azurerm_resource_group.cc.name
  location            = azurerm_resource_group.cc.location
  subnet_id           = azurerm_subnet.cc.id
  admin_username      = var.ssh_user
  ssh_public_key      = local.ssh_key
  tags                = merge(local.tags, { role = "twin" })
}

resource "azurerm_consumption_budget_resource_group" "cc" {
  count             = length(var.budget_contact_emails) > 0 ? 1 : 0
  name              = "attestland-cc"
  resource_group_id = azurerm_resource_group.cc.id
  amount            = var.budget_usd
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = "Actual"
      contact_emails = var.budget_contact_emails
    }
  }
}
