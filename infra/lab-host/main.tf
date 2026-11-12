# Lab host (phase 1, ADR 011): one c5.metal running Ubuntu 22.04 on the bare
# hardware, for the SONiC fabric (make lab-up) and the provisioning
# simulation (make provision), which needs /dev/kvm. Its own small VPC, so it
# comes and goes without the GPU pool. Tagged for the 8-hour reaper.
terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project = "attestland"
      phase   = "1"
      owner   = var.owner
      keep    = var.keep ? "true" : "false"
    }
  }
}

# Ubuntu 22.04 (ADR 009).
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

locals {
  versions = { for l in regexall("(?m)^([A-Z0-9_]+)=([^\\s#]+)", file("${path.module}/../../versions.env")) : l[0] => l[1] }
  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    ssh_user       = var.ssh_user
    ssh_public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
    containerlab   = local.versions.CONTAINERLAB_VERSION
    k3d            = local.versions.K3D_VERSION
    helm           = local.versions.HELM_VERSION
    kubectl        = local.versions.KUBERNETES_VERSION
  })
}

resource "aws_vpc" "lab" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  tags                 = { Name = "al-lab" }
}

resource "aws_internet_gateway" "lab" {
  vpc_id = aws_vpc.lab.id
  tags   = { Name = "al-lab" }
}

resource "aws_route_table" "lab" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }
  tags = { Name = "al-lab" }
}

resource "aws_subnet" "lab" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 0)
  availability_zone       = var.az
  map_public_ip_on_launch = true
  tags                    = { Name = "al-lab-${var.az}" }
}

resource "aws_route_table_association" "lab" {
  subnet_id      = aws_subnet.lab.id
  route_table_id = aws_route_table.lab.id
}

# SSH only. The lab fabric, Redfish, Tinkerbell and the provisioning VMs all
# stay on the host's own bridges; reach them through an SSH tunnel.
resource "aws_security_group" "lab" {
  name        = "al-lab"
  description = "Attestland lab host"
  vpc_id      = aws_vpc.lab.id
  tags        = { Name = "al-lab" }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each          = toset(var.admin_cidrs)
  security_group_id = aws_security_group.lab.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.lab.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_instance" "lab" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.lab.id
  vpc_security_group_ids = [aws_security_group.lab.id]
  user_data              = local.user_data
  # Disks for the provisioning VMs (20 GB each) and container images.
  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }
  metadata_options {
    http_tokens = "required"
  }
  # Bare-metal boots take 5-15 minutes; the default 10-minute create timeout is too short.
  timeouts {
    create = "30m"
  }
  tags = { Name = "al-lab", role = "lab" }
}
