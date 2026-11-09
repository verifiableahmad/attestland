# GPU node image: driver, CUDA, container toolkit, NCCL, DCGM, Enroot/Pyxis
# baked in, so paid GPU time is not spent installing. Built on a CPU VM.
packer {
  required_plugins {
    googlecompute = {
      source  = "github.com/hashicorp/googlecompute"
      version = "~> 1.1"
    }
    ansible = {
      source  = "github.com/hashicorp/ansible"
      version = "~> 1.1"
    }
  }
}

variable "project_id" { type = string }
variable "zone" {
  type    = string
  default = "us-central1-a"
}
variable "versions_tag" {
  type        = string
  description = "Short tag derived from versions.env, e.g. drv580-cuda129."
}

source "googlecompute" "gpu" {
  project_id          = var.project_id
  zone                = var.zone
  source_image_family = "ubuntu-2204-lts"
  machine_type        = "n2-standard-8"
  disk_size           = 60
  ssh_username        = "packer"
  image_name          = "al-gpu-ubuntu-2204-${var.versions_tag}-{{timestamp}}"
  image_family        = "al-gpu-ubuntu-2204"
  image_labels = {
    project = "attestland"
    keep    = "true"
  }
}

build {
  sources = ["source.googlecompute.gpu"]

  provisioner "ansible" {
    playbook_file = "${path.root}/../../ansible/image.yml"
    user          = "packer"
    extra_arguments = [
      "--extra-vars", "@${path.root}/../../ansible/group_vars/all/versions.yml",
      "--extra-vars", "image_build=true",
    ]
  }
}
