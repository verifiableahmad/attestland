output "bucket_prefix" {
  value = local.bucket_prefix
}

# Read by Ansible (group_vars/all s3_access_key / s3_secret_key) for /etc/fleet/s3.env.
output "s3_access_key_id" {
  value = nebius_iam_v2_access_key.s3.status.aws_access_key_id
}

output "s3_secret_access_key" {
  value     = nebius_iam_v2_access_key.s3.status.secret
  sensitive = true
}
