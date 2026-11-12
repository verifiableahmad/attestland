output "public_ip" {
  value = aws_instance.lab.public_ip
}

output "ssh" {
  value = "ssh ${var.ssh_user}@${aws_instance.lab.public_ip}"
}
