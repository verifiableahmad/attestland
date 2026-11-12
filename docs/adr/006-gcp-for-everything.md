# ADR 006: Google Cloud for every pool; AWS second; Azure removed

- Status: superseded by ADR 010 (GCP L4 quota was blocked; the GPU pool is on AWS, the confidential pool on Azure)
- Date: 2026-11-08
- Phase / section: Phases 2–5, spec 7.2–7.7

## Decision

Both pools run on Google Cloud, in one project. The GPU pool stays on
`g2-standard-8` (L4). The confidential pool moves from Azure
`Standard_NCC40ads_H100_v5` (AMD SEV-SNP) to GCP `a3-highgpu-1g` Confidential
VM (Intel TDX + 1x H100 80GB in CC mode), with Google Cloud Attestation (GCA)
for the CPU token and NVIDIA NRAS for the GPU, as before. AWS is the second
choice for the GPU pool. AWS sells no confidential GPU. The Azure pool,
its Microsoft Azure Attestation (MAA) verifier and its policies were removed
from the repo (they remain in git history before the commit that removed them).

## Context

The owner chose GCP first and AWS second. One cloud means one account, one
quota process, one budget and one reaper identity. Confidential A3 High is
generally available in us-central1-a, us-east5-a and europe-west4-c, but only
as Spot or flex-start: no on-demand and no reservations. No AWS confidential
GPU instance was found in AWS documentation or third-party comparisons as of
November 2026.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| GCP a3-highgpu-1g Confidential VM (chosen) | Same project as the GPU pool; twin is the same machine type without TDX, a cleaner comparison than Azure's twin | Spot or flex-start only; Google documents GCA's TDX support for C3, not A3 (unconfirmed); different TEE from the spec's SEV-SNP |
| Azure NCC40ads_H100_v5 (spec; removed) | On-demand | Second cloud account; long quota lead time; no fallback is kept, so a GCA refusal means the raw-quote route below |
| AWS | Owner's second choice | No confidential GPU offering |
| Smaller GPU clouds (Phala, VoltageGPU, others) | Confidential H100/H200 listed | Claims mostly from the vendors' own pages; unclear whether we control the attestation flow |

## Consequences

- `alverify` checks Google Cloud Attestation tokens: the policy's `cpu.verifier`
  is `gca` (the default and only value). Every receipt test, including all six
  negative tests, runs against it.
- `alissue --platform gcp` (default) runs `gotpm token --tee-technology tdx`
  and also embeds a raw TDX quote over the nonce, so a later verifier can check
  the quote directly.
- Receipts now describe Intel TDX on an H100 SXM 80GB, not
  SEV-SNP on an H100 NVL.
- Spot preemption can stop a benchmark repeat. The harness repeats
  configurations, so a lost repeat is rerun, not patched.
- Revisit if GCA does not issue TDX tokens on a3-highgpu-1g. If so, either
  verify the raw TDX quote (Intel DCAP or Intel Trust Authority, a v2 item)
  or restore the Azure path from git history.
- An AWS GPU pool (`g6.2xlarge`, 1x L4, the closest match to `g2-standard-8`)
  is not built. Build it only if GCP L4 quota is refused.
