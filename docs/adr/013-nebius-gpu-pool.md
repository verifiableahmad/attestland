# ADR 013: Nebius as a second cloud for the GPU pool

- Status: accepted
- Date: 2026-11-16
- Phase / section: Phases 2-4, spec 5.1
- Amends: ADR 010 (AWS stays the default GPU pool), ADR 009 (Ubuntu 24.04 on Nebius)

## Decision

The GPU pool can run on Nebius AI Cloud as well as AWS:
`make up POOL=gpu CLOUD=nebius` applies `infra/gpu-pool-nebius` and configures
it with the same `ansible/site-gpu.yml`. The layout is the same (a CPU control
VM plus single-GPU nodes, default 2× L40S `gpu-l40s-a` / `1gpu-8vcpu-32gb` in
eu-north1). The inventory goes to `ansible/inventory/gpu-nebius.ini`, so an AWS
pool and a Nebius pool can exist at the same time. A training or serving job
always stays inside one cloud; nothing spans the two.

## Context

AWS gave this account 8 on-demand G vCPUs (one g6.2xlarge) after the limit
dropped to 0 on 2026-11-15, and raising it means a support case of unknown
length. Nebius gives a new account 8 L40S GPUs in eu-north1 without a ticket,
at $1.35 per GPU-hour on demand. Its default tier is tight elsewhere: 2
regular and 2 preemptible VMs, 4 disks, and 0 non-GPU vCPUs per region in
eu-north1, so the control VM needs a quota request there too.

## Consequences

- Ubuntu 24.04 on Nebius: its 22.04 image families no longer create new disks.
  The pool uses `ubuntu24.04-driverless`, and `nvidia_driver` picks the CUDA
  repository matching the node's release. AWS stays on 22.04.
- No instance role: the nodes reach Nebius Object Storage
  (`https://storage.eu-north1.nebius.cloud`) with the S3 key of a service
  account in the tenant's editors group. Ansible reads it from the tofu
  outputs (`s3_access_key_id`, `s3_secret_access_key`), never from the
  inventory, and writes it to `/etc/fleet/s3.env` (mode 0640) and to the
  `serving/s3-credentials` secret. The key is also in the local tofu state.
- No instance store: `/scratch` is a directory on a 400 GiB network SSD boot
  disk, so `ckpt-bench` results from Nebius are not comparable with AWS.
- Topology stays flat (`unknown`): Nebius reports positions only for
  GPU-cluster (InfiniBand) VMs, and L40S cannot join one.
- The provider is not on the OpenTofu registry; the module names it as
  `registry.terraform.io/nebius/nebius`.
- Not ported yet: the reaper (`infra/reaper`) and `chaos/inject.sh` node-loss
  only know AWS and Azure, and there is no budget resource. A Nebius pool
  must be destroyed by hand (`make down POOL=gpu CLOUD=nebius`); a prepaid
  balance caps the cost.
