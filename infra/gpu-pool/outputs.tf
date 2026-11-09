output "ansible_inventory" {
  sensitive = true
  value = templatefile("${path.module}/inventory.tftpl", {
    ssh_user = var.ssh_user
    region   = var.region
    gpu_type = var.gpu_machine_type
    control  = { name = google_compute_instance.control.name, public = google_compute_instance.control.network_interface[0].access_config[0].nat_ip, private = google_compute_instance.control.network_interface[0].network_ip }
    s3       = { endpoint = "https://storage.googleapis.com", access_key = google_storage_hmac_key.nodes.access_id, secret_key = google_storage_hmac_key.nodes.secret, prefix = local.bucket_prefix }
    gpus     = [for g in google_compute_instance.gpu : { name = g.name, public = g.network_interface[0].access_config[0].nat_ip, private = g.network_interface[0].network_ip }]
  })
}

output "control_ip" {
  value = google_compute_instance.control.network_interface[0].access_config[0].nat_ip
}
