output "ansible_inventory" {
  value = templatefile("${path.module}/inventory.tftpl", {
    ssh_user        = var.ssh_user
    ssh_private_key = trimsuffix(pathexpand(var.ssh_public_key_path), ".pub")
    region          = var.region
    gpu_type        = var.gpu_instance_type
    control_type    = var.control_instance_type
    control         = { name = aws_instance.control.tags.Name, id = aws_instance.control.id, public = aws_instance.control.public_ip, private = aws_instance.control.private_ip }
    prefix          = local.bucket_prefix
    gpus            = [for g in aws_instance.gpu : { name = g.tags.Name, id = g.id, public = g.public_ip, private = g.private_ip }]
    cloud           = "aws"
    s3_endpoint     = "" # the S3 default; credentials from the instance role
  })
}

output "control_ip" {
  value = aws_instance.control.public_ip
}
