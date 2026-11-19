# Durable tier on Nebius (ADR 013): Object Storage buckets and the service-account
# S3 key the nodes use. Its own root module and state, like infra/durable on AWS,
# so `make down POOL=gpu CLOUD=nebius` (infra/gpu-pool-nebius) neither deletes the
# data nor stops on a non-empty bucket. Apply once: `make durable-nebius-up`.
terraform {
  required_version = ">= 1.10"
  required_providers {
    nebius = {
      source  = "registry.terraform.io/nebius/nebius"
      version = "~> 0.6.8"
    }
  }
  # State in the AWS state bucket of infra/durable (the only thing here that
  # needs AWS): the Makefile passes bucket and region. Holds the S3 secret key.
  backend "s3" {
    key          = "durable-nebius/terraform.tfstate"
    use_lockfile = true
  }
}

provider "nebius" {
  profile = { name = var.cli_profile }
}
