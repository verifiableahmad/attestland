# ADR 010: AWS for the GPU pool, Azure for the confidential pool; GCP removed

- Status: accepted (supersedes ADR 006 and ADR 008)
- Date: 2026-11-10
- Phase / section: Phases 2–5, spec 5.1, 7.2–7.7

## Decision

- **GPU pool (phases 2–4): AWS**, `g6.2xlarge` (1x L4, 8 vCPU, 32 GiB, 450 GB
  NVMe instance store) in us-east-1, all VMs in one zone (`var.az`), with a
  cluster placement group on on-demand runs. The control VM is `m7i.xlarge`.
- **Confidential pool (phase 5): Azure**, `Standard_NCC40ads_H100_v5` (AMD
  SEV-SNP + 1x H100 NVL in CC mode) in eastus2 zone 1, on-demand. The CPU token
  comes from Microsoft Azure Attestation (MAA), the GPU result from NVIDIA NRAS
  as before. The non-CC twin is `Standard_NC40ads_H100_v5`.
- **Durable tier: S3**, four versioned buckets (`<prefix>-datasets`,
  `-checkpoints`, `-models`, `-receipts`) reached with the nodes' instance role.
  No HMAC or access keys.
- **GCP is removed**: both GCP pools, the Google Cloud Attestation collector and
  verifier, the raw TDX quote, the GCE topology source and the GCP reaper pass.
  They are kept on the `gcp-archive` branch. The Azure path removed by ADR 006
  was restored from the `azure-archive` branch and updated.

## Context

GCP's L4 quota request was blocked, so the GPU pool could not start there. On
2026-11-10:

- The AWS account had 384 vCPUs of on-demand G and VT quota in us-east-1 (and
  eu-west-1, which has no g6), and 0 Spot quota. A test `g6.2xlarge` launch
  worked in us-east-1a with 1x L4 and a working driver; us-east-1b returned
  `InsufficientInstanceCapacity`. us-east-1 had no default VPC.
- AWS sells no confidential GPU: no instance type with a GPU lists AMD SEV-SNP
  (`aws ec2 describe-instance-types`), so the confidential pool needs another cloud.
- Azure granted 40 vCPUs of `StandardNCCads2023Family` quota in eastus2 (support
  request 2611100050001296, approved within an hour). The size is offered to the
  subscription in eastus2 (zone 1), centralus (zone 3) and westeurope (zone 1).

On GCP, confidential A3 was Spot or flex-start only, and Google documents
Google Cloud Attestation's TDX support for C3, not A3 (ADR 006's open risk).
Azure documents SEV-SNP + MAA for the NCC H100 v5 series, on-demand.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| AWS GPU pool + Azure confidential pool (chosen) | Both quotas exist today; on-demand confidential VM; documented CPU attestation; native S3 drops the HMAC key and the checksum workaround | Two clouds: two accounts, budgets and reaper passes; no topology data on g6.2xlarge; slower network than g2 |
| AWS GPU pool + GCP confidential pool (hybrid) | Receipt code already written for GCP | Needs GCP H100 quota on the same account whose L4 quota was blocked; Spot only; GCA on A3 unconfirmed |
| Keep GCP for both, wait for quota | One cloud (ADR 006's reason) | No date; nothing runs meanwhile |
| Keep GCP code beside AWS and Azure | A fallback if GCP quota arrives | About twice the infra, reaper and verifier surface to keep working, untested |

## Consequences

- **Receipts** describe AMD SEV-SNP on an H100 NVL (`cloud: azure`). `alverify`
  checks MAA tokens only (`cpu.verifier: maa`): attestation type `sevsnpvm`,
  not debuggable, the compliance status and, once pinned, the launch
  measurement. The six negative tests and two CPU policy tests (launch
  measurement off the allow-list, wrong compliance status) run against an
  MAA-shaped test platform. The raw TDX quote is gone; raw SEV-SNP report
  verification remains a v2 item.
- **Topology (ADR 007)**: EC2 reports instance topology only for some larger
  types. `DescribeInstanceTopology` returned nothing for a running g6.2xlarge,
  so Slurm and Kueue see one flat group on this size. The plumbing (now fed by
  EC2 topology) works unchanged on a covered type.
- **Network**: g6.2xlarge has a 5 Gbps sustained baseline (10 Gbps burst) and no
  EFA, against 16 Gbps on g2-standard-8. NCCL-over-TCP results will be lower;
  burn-in baselines use 5 Gbps. EFA starts at g6.8xlarge (25 Gbps, 32 vCPU).
- **Spot**: the account has 0 Spot quota for G instances, so `spot` defaults to
  false and development runs pay on-demand prices. Request Spot quota to change it.
- **Twin**: `Standard_NC40ads_H100_v5` is `NotAvailableForSubscription` in all
  three regions above. The overhead benchmark (7.7) needs an Azure region/SKU
  access request first; everything else in phase 5 does not.
- **Operations**: the reaper has an AWS pass (GitHub OIDC role) and an Azure pass
  (federated identity); budgets are AWS Budgets (needs the `project` cost
  allocation tag) and an Azure resource group budget. Chaos node-loss stops EC2
  instances from the control VM, which gets the AWS CLI.
- **Cross-cloud data**: the trained model moves from S3 to the Azure VM by hand
  (`aws s3 sync`, then copy). It is about 500 MB for the 124M model.
- Phase 2–4 numbers will not be comparable with any GCP figure.
- Revisit if Azure refuses the twin's SKU access (then compare against a
  non-CC run on another subscription or region), or if GCP quota arrives and one
  cloud matters more than the points above.
