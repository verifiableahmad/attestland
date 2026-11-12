# One H100 VM. confidential = true gives a SEV-SNP confidential VM with secure
# boot, vTPM and confidential OS disk encryption (VM guest state + OS disk).
terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
  }
}

variable "name" { type = string }
variable "size" { type = string }
variable "confidential" { type = bool }
variable "zone" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "subnet_id" { type = string }
variable "admin_username" { type = string }
variable "ssh_public_key" { type = string }
variable "tags" { type = map(string) }

locals {
  # Ubuntu 22.04 is the qualified image for Azure confidential GPU VMs (ADR 009).
  image = var.confidential ? {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-confidential-vm-jammy"
    sku       = "22_04-lts-cvm"
    } : {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
  }
}

resource "azurerm_public_ip" "this" {
  name                = "${var.name}-ip"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = [var.zone]
  tags                = var.tags
}

resource "azurerm_network_interface" "this" {
  name                = "${var.name}-nic"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = var.subnet_id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.this.id
  }
}

resource "azurerm_linux_virtual_machine" "this" {
  name                  = var.name
  location              = var.location
  resource_group_name   = var.resource_group_name
  size                  = var.size
  zone                  = var.zone
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.this.id]
  tags                  = var.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching                  = "ReadWrite"
    storage_account_type     = "Premium_LRS"
    disk_size_gb             = 256
    security_encryption_type = var.confidential ? "DiskWithVMGuestState" : null
  }

  source_image_reference {
    publisher = local.image.publisher
    offer     = local.image.offer
    sku       = local.image.sku
    version   = "latest"
  }

  secure_boot_enabled = true
  vtpm_enabled        = true
}

output "public_ip" { value = azurerm_public_ip.this.ip_address }
output "private_ip" { value = azurerm_network_interface.this.private_ip_address }
output "name" { value = azurerm_linux_virtual_machine.this.name }
