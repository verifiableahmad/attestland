# GPU pool on Nebius (ADR 013): the same layout as infra/gpu-pool (a control VM
# plus single-GPU nodes), selected with `make up POOL=gpu CLOUD=nebius`. The
# durable tier is Nebius Object Storage (storage.tf), reached with an S3 key.
terraform {
  required_version = ">= 1.8"
  required_providers {
    # Not on registry.opentofu.org: the full address makes OpenTofu fetch it from
    # the Terraform registry.
    nebius = {
      source  = "registry.terraform.io/nebius/nebius"
      version = ">= 0.6.8"
    }
  }
}

# Credentials: the Nebius CLI profile (`nebius profile create`).
provider "nebius" {
  profile = { name = var.cli_profile }
}

# Nebius has no Ubuntu 22.04 image for new disks any more, so this pool runs
# 24.04 (ADR 013). The driverless family: the nvidia_driver role installs the
# pinned driver, as on AWS.
locals {
  user_data = <<-EOT
    #cloud-config
    users:
      - default
      - name: ${var.ssh_user}
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        ssh_authorized_keys:
          - ${trimspace(file(pathexpand(var.ssh_public_key_path)))}
  EOT
  labels = {
    project = "attestland"
    phase   = tostring(var.phase)
    owner   = var.owner
    keep    = var.keep ? "true" : "false"
  }
  # The last preemptible_gpu_nodes GPU nodes are preemptible (separate quota).
  preemptible = { for i in range(var.gpu_nodes) : i => i >= var.gpu_nodes - var.preemptible_gpu_nodes }
}

# The project's default network and subnet; a security group of our own on it.
data "nebius_vpc_v1_default_network" "al" {
  parent_id = var.project_id
}

resource "nebius_vpc_v1_security_group" "al" {
  parent_id  = var.project_id
  name       = "al-gpu"
  network_id = data.nebius_vpc_v1_default_network.al.network.metadata.id
}

resource "nebius_vpc_v1_security_rule" "internal" {
  parent_id = nebius_vpc_v1_security_group.al.id
  name      = "internal"
  access    = "ALLOW"
  protocol  = "ANY"
  ingress   = { source_security_group_id = nebius_vpc_v1_security_group.al.id }
}

resource "nebius_vpc_v1_security_rule" "admin" {
  parent_id = nebius_vpc_v1_security_group.al.id
  name      = "admin"
  access    = "ALLOW"
  protocol  = "TCP"
  ingress   = { source_cidrs = var.admin_cidrs, destination_ports = [22, 3000, 6443] } # ssh, grafana, k8s api
}

resource "nebius_vpc_v1_security_rule" "egress" {
  parent_id = nebius_vpc_v1_security_group.al.id
  name      = "egress"
  access    = "ALLOW"
  protocol  = "ANY"
  egress    = { destination_cidrs = ["0.0.0.0/0"] }
}

locals {
  network_interface = {
    name              = "eth0"
    subnet_id         = data.nebius_vpc_v1_default_network.al.subnet.metadata.id
    ip_address        = {}
    public_ip_address = {}
    security_groups   = [{ id = nebius_vpc_v1_security_group.al.id }]
  }
}

resource "nebius_compute_v1_instance" "control" {
  parent_id            = var.project_id
  name                 = "al-control"
  hostname             = "al-control"
  labels               = merge(local.labels, { role = "control" })
  resources            = { platform = var.control_platform, preset = var.control_preset }
  network_interfaces   = [local.network_interface]
  cloud_init_user_data = local.user_data
  boot_disk = {
    attach_mode = "READ_WRITE"
    managed_disk = {
      name = "al-control-boot"
      spec = {
        type                = "NETWORK_SSD"
        size_gibibytes      = 100
        source_image_family = { image_family = var.cpu_image_family }
      }
    }
  }
}

# No local NVMe on these presets: /scratch lives on the boot disk (role scratch
# keeps it there when it finds no instance store), so the disk is larger than on AWS.
resource "nebius_compute_v1_instance" "gpu" {
  count                = var.gpu_nodes
  parent_id            = var.project_id
  name                 = format("al-gpu-%02d", count.index + 1)
  hostname             = format("al-gpu-%02d", count.index + 1)
  labels               = merge(local.labels, { role = "gpu" })
  resources            = { platform = var.gpu_platform, preset = var.gpu_preset }
  network_interfaces   = [local.network_interface]
  cloud_init_user_data = local.user_data
  preemptible          = local.preemptible[count.index] ? { on_preemption = "STOP" } : null
  boot_disk = {
    attach_mode = "READ_WRITE"
    managed_disk = {
      name = format("al-gpu-%02d-boot", count.index + 1)
      spec = {
        type                = "NETWORK_SSD"
        size_gibibytes      = var.gpu_disk_gib
        source_image_family = { image_family = var.gpu_image_family }
      }
    }
  }
}
