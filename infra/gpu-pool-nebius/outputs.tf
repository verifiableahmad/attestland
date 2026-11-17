locals {
  ip = { for name, vm in merge({ control = nebius_compute_v1_instance.control }, { for g in nebius_compute_v1_instance.gpu : g.name => g }) :
    name => { public = vm.status.network_interfaces[0].public_ip_address.address, private = vm.status.network_interfaces[0].ip_address.address }
  }
  # Strip a prefix length if the address comes with one (a.b.c.d/32).
  host = { for k, v in local.ip : k => { public = split("/", v.public)[0], private = split("/", v.private)[0] } }
}

output "ansible_inventory" {
  value = templatefile("${path.module}/../gpu-pool/inventory.tftpl", {
    ssh_user        = var.ssh_user
    ssh_private_key = trimsuffix(pathexpand(var.ssh_public_key_path), ".pub")
    region          = var.region
    gpu_type        = "${var.gpu_platform}/${var.gpu_preset}"
    control_type    = "${var.control_platform}/${var.control_preset}"
    control         = { name = nebius_compute_v1_instance.control.name, id = nebius_compute_v1_instance.control.id, public = local.host.control.public, private = local.host.control.private }
    prefix          = local.bucket_prefix
    gpus            = [for g in nebius_compute_v1_instance.gpu : { name = g.name, id = g.id, public = local.host[g.name].public, private = local.host[g.name].private }]
    cloud           = "nebius"
    s3_endpoint     = "https://storage.${var.region}.nebius.cloud"
  })
}

output "control_ip" {
  value = local.host.control.public
}

# Read by Ansible (group_vars/all s3_access_key / s3_secret_key) for /etc/fleet/s3.env.
output "s3_access_key_id" {
  value = nebius_iam_v2_access_key.s3.status.aws_access_key_id
}

output "s3_secret_access_key" {
  value     = nebius_iam_v2_access_key.s3.status.secret
  sensitive = true
}
