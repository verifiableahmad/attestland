# GPU pool (phases 2-4, ADR 010): 4 x g6.2xlarge (1x L4), a control VM (Slurm + k8s
# control plane + monitoring); the durable tier is S3 buckets (storage.tf).
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
      phase   = tostring(var.phase)
      owner   = var.owner
      keep    = var.keep ? "true" : "false"
    }
  }
}

data "aws_caller_identity" "current" {}

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
  # cloud-init: the same admin user as the confidential pool, instead of the AMI's default.
  user_data    = <<-EOT
    #cloud-config
    users:
      - default
      - name: ${var.ssh_user}
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        ssh_authorized_keys:
          - ${trimspace(file(pathexpand(var.ssh_public_key_path)))}
  EOT
  subnet_cidrs = { for i, az in var.azs : az => cidrsubnet(var.vpc_cidr, 8, i) }
  subnet_cidr  = local.subnet_cidrs[var.az]
}

resource "aws_vpc" "al" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  tags                 = { Name = "al-gpu" }
}

resource "aws_internet_gateway" "al" {
  vpc_id = aws_vpc.al.id
  tags   = { Name = "al-gpu" }
}

resource "aws_route_table" "al" {
  vpc_id = aws_vpc.al.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.al.id
  }
  tags = { Name = "al-gpu" }
}

# One subnet per zone, so moving the pool to another zone when one runs out of
# g6 capacity is a change of var.az, not of the network.
resource "aws_subnet" "al" {
  for_each                = local.subnet_cidrs
  vpc_id                  = aws_vpc.al.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true
  tags                    = { Name = "al-gpu-${each.key}" }
}

resource "aws_route_table_association" "al" {
  for_each       = aws_subnet.al
  subnet_id      = each.value.id
  route_table_id = aws_route_table.al.id
}

resource "aws_security_group" "al" {
  name        = "al-gpu"
  description = "Attestland GPU pool"
  vpc_id      = aws_vpc.al.id
  tags        = { Name = "al-gpu" }
}

resource "aws_vpc_security_group_ingress_rule" "internal" {
  security_group_id = aws_security_group.al.id
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "admin" {
  for_each          = { for p in setproduct(var.admin_cidrs, [22, 3000, 6443]) : "${p[0]}-${p[1]}" => p } # ssh, grafana, k8s api
  security_group_id = aws_security_group.al.id
  cidr_ipv4         = each.value[0]
  ip_protocol       = "tcp"
  from_port         = each.value[1]
  to_port           = each.value[1]
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.al.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_instance" "control" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.control_instance_type
  subnet_id              = aws_subnet.al[var.az].id
  private_ip             = cidrhost(local.subnet_cidr, 10)
  vpc_security_group_ids = [aws_security_group.al.id]
  iam_instance_profile   = aws_iam_instance_profile.nodes.name
  user_data              = local.user_data
  root_block_device {
    volume_size = 100
    volume_type = "gp3"
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  tags = { Name = "al-control", role = "control" }
}

# Cluster placement (ADR 007): EC2 puts the GPU VMs close together in one zone.
# Only for on-demand runs (the timed benchmarks). EC2 reports position
# (DescribeInstanceTopology) only for some larger types, not g6.2xlarge, so the
# scheduler topology stays flat on this size.
locals {
  cluster = var.cluster_placement && !var.spot
}

resource "aws_placement_group" "gpu" {
  count    = local.cluster ? 1 : 0
  name     = "al-gpu-cluster"
  strategy = "cluster"
}

resource "aws_instance" "gpu" {
  count                  = var.gpu_nodes
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.gpu_instance_type
  subnet_id              = aws_subnet.al[var.az].id
  private_ip             = cidrhost(local.subnet_cidr, 100 + count.index + 1)
  vpc_security_group_ids = [aws_security_group.al.id]
  iam_instance_profile   = aws_iam_instance_profile.nodes.name
  placement_group        = local.cluster ? aws_placement_group.gpu[0].id : null
  user_data              = local.user_data
  root_block_device {
    volume_size = 200
    volume_type = "gp3"
  }
  # The local NVMe instance store (/scratch fast tier) attaches on its own.
  dynamic "instance_market_options" {
    for_each = var.spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        instance_interruption_behavior = "stop"
        spot_instance_type             = "persistent"
      }
    }
  }
  # Pods reach the node role through IMDS (vLLM weight sync), one hop further away.
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  tags = { Name = format("al-gpu-%02d", count.index + 1), role = "gpu" }
}

# Node role: the durable buckets (storage.tf), and chaos "node loss" stopping
# and starting pool VMs from the control VM; topology lookups for ADR 007.
resource "aws_iam_role" "nodes" {
  name = "al-nodes"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_instance_profile" "nodes" {
  name = "al-nodes"
  role = aws_iam_role.nodes.name
}

resource "aws_iam_role_policy" "chaos" {
  name = "chaos-and-topology"
  role = aws_iam_role.nodes.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ec2:DescribeInstances", "ec2:DescribeInstanceTopology"], Resource = "*" },
      {
        Effect    = "Allow", Action = ["ec2:StopInstances", "ec2:StartInstances"], Resource = "*"
        Condition = { StringEquals = { "ec2:ResourceTag/project" = "attestland" } }
      },
    ]
  })
}

resource "aws_budgets_budget" "al" {
  count        = length(var.budget_contact_emails) > 0 ? 1 : 0
  name         = "attestland"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  # The project tag must be activated as a cost allocation tag (runbook).
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$attestland"]
  }
  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.budget_contact_emails
    }
  }
}
