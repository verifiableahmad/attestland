# Confidential pool (phase 5): one GCP a3-highgpu-1g Confidential VM (Intel TDX
# + 1x H100 80GB with NVIDIA CC on) and, for the overhead benchmark, its
# non-confidential twin (same machine type, no TDX). Create the twin only when
# needed. Confidential A3 High offers Spot and flex-start only (no on-demand,
# no reservations), so both VMs are Spot for a like-for-like comparison.
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
    phase   = "5"
    owner   = var.owner
    keep    = var.keep ? "true" : "false"
  }
  ssh_keys = "${var.ssh_user}:${trimspace(file(pathexpand(var.ssh_public_key_path)))}"
  vms = merge(
    { "al-cc-h100" = { confidential = true, role = "cc" } },
    var.create_twin ? { "al-noncc-h100" = { confidential = false, role = "twin" } } : {},
  )
}

# gotpm sends evidence to Google Cloud Attestation through this API.
resource "google_project_service" "attestation" {
  service            = "confidentialcomputing.googleapis.com"
  disable_on_destroy = false
}

resource "google_compute_network" "cc" {
  name                    = "al-cc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "cc" {
  name          = "al-cc-${var.region}"
  ip_cidr_range = var.subnet_cidr
  network       = google_compute_network.cc.id
  region        = var.region
}

resource "google_compute_firewall" "admin" {
  name          = "al-cc-admin"
  network       = google_compute_network.cc.name
  source_ranges = var.admin_cidrs
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_instance" "h100" {
  for_each     = local.vms
  name         = each.key
  machine_type = var.machine_type
  labels       = merge(local.labels, { role = each.value.role })

  boot_disk {
    initialize_params {
      image = var.image
      size  = 256
      type  = var.boot_disk_type
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.cc.id
    nic_type   = "GVNIC"
    access_config {}
  }
  dynamic "confidential_instance_config" {
    for_each = each.value.confidential ? [1] : []
    content { confidential_instance_type = "TDX" }
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  scheduling {
    on_host_maintenance         = "TERMINATE" # no live migration for TDX or GPUs
    automatic_restart           = false
    preemptible                 = true
    provisioning_model          = "SPOT"
    instance_termination_action = "STOP"
  }
  metadata   = { ssh-keys = local.ssh_keys }
  depends_on = [google_project_service.attestation]
}

resource "google_billing_budget" "cc" {
  count           = var.billing_account_id == "" ? 0 : 1
  billing_account = var.billing_account_id
  display_name    = "attestland-cc"
  budget_filter {
    projects = ["projects/${var.project_number}"]
    labels   = { phase = "5" }
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
