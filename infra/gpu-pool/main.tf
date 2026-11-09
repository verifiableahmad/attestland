# GPU pool (phases 2-4): 4 x g2-standard-8 (1x L4), a control VM (Slurm + k8s
# control plane + monitoring); the durable tier is Cloud Storage buckets (storage.tf).
terraform {
  required_version = ">= 1.8"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id

  # Billing Budgets needs a quota project; ADC does not set one.
  user_project_override = true
  billing_project       = var.project_id
  region                = var.region
  zone                  = var.zone
}

locals {
  labels = {
    project = "attestland"
    phase   = tostring(var.phase)
    owner   = var.owner
    keep    = var.keep ? "true" : "false"
  }
  ssh_keys = "${var.ssh_user}:${trimspace(file(pathexpand(var.ssh_public_key_path)))}"
}

resource "google_compute_network" "al" {
  name                    = "al-gpu"
  auto_create_subnetworks = false
  mtu                     = 8896 # jumbo frames for NCCL over TCP
}

resource "google_compute_subnetwork" "al" {
  name          = "al-gpu-${var.region}"
  ip_cidr_range = var.subnet_cidr
  network       = google_compute_network.al.id
  region        = var.region
}

resource "google_compute_firewall" "internal" {
  name          = "al-gpu-internal"
  network       = google_compute_network.al.name
  source_ranges = [var.subnet_cidr]
  allow { protocol = "all" }
}

resource "google_compute_firewall" "admin" {
  name          = "al-gpu-admin"
  network       = google_compute_network.al.name
  source_ranges = var.admin_cidrs
  allow {
    protocol = "tcp"
    ports    = ["22", "3000", "6443"] # ssh, grafana, k8s api
  }
}

resource "google_compute_instance" "control" {
  name         = "al-control"
  machine_type = var.control_machine_type
  labels       = merge(local.labels, { role = "control" })
  tags         = ["al-control"]

  boot_disk {
    initialize_params {
      image = var.cpu_image
      size  = 100
      type  = "pd-balanced"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.al.id
    network_ip = cidrhost(var.subnet_cidr, 10)
    access_config {}
  }
  metadata = { ssh-keys = local.ssh_keys }
  service_account {
    email  = google_service_account.nodes.email
    scopes = ["cloud-platform"]
  }
}

# Compact placement (ADR 007): GCE places the GPU VMs physically close and
# exposes each VM's block / sub-block, which becomes Slurm and Kueue topology.
# Only for on-demand runs: Spot with compact placement is not documented.
locals {
  compact = var.compact_placement && !var.spot
}

resource "google_compute_resource_policy" "gpu_compact" {
  count  = local.compact ? 1 : 0
  name   = "al-gpu-compact"
  region = var.region
  group_placement_policy {
    collocation = "COLLOCATED"
  }
}

resource "google_compute_instance" "gpu" {
  count        = var.gpu_nodes
  name         = format("al-gpu-%02d", count.index + 1)
  machine_type = var.gpu_machine_type
  labels       = merge(local.labels, { role = "gpu" })

  boot_disk {
    initialize_params {
      image = var.gpu_image
      size  = 200
      type  = "pd-balanced"
    }
  }
  scratch_disk { interface = "NVME" } # /scratch fast tier
  resource_policies = local.compact ? [google_compute_resource_policy.gpu_compact[0].id] : []
  network_interface {
    subnetwork = google_compute_subnetwork.al.id
    network_ip = cidrhost(var.subnet_cidr, 100 + count.index + 1)
    nic_type   = "GVNIC"
    access_config {}
  }
  scheduling {
    on_host_maintenance         = "TERMINATE"
    automatic_restart           = !var.spot
    preemptible                 = var.spot
    provisioning_model          = var.spot ? "SPOT" : "STANDARD"
    instance_termination_action = var.spot ? "STOP" : null
  }
  metadata = { ssh-keys = local.ssh_keys }
  service_account {
    email  = google_service_account.nodes.email
    scopes = ["cloud-platform"]
  }
}

resource "google_service_account" "nodes" {
  account_id   = "al-nodes"
  display_name = "Attestland nodes"
}

# Chaos "node loss" stops VMs through the provider API from the control VM.
resource "google_project_iam_member" "chaos_stop" {
  project = var.project_id
  role    = "roles/compute.instanceAdmin.v1"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_billing_budget" "al" {
  count           = var.billing_account_id == "" ? 0 : 1
  billing_account = var.billing_account_id
  display_name    = "attestland"
  budget_filter {
    projects = ["projects/${var.project_number}"]
    labels   = { project = "attestland" }
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_usd)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.8, 1.0]
    content { threshold_percent = threshold_rules.value }
  }
}
