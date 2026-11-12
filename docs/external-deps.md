# External dependencies and assumptions

Snapshot of 2026-11-10.

## External dependencies

21 external dependencies stand between the repo and measured results: 2 are fully in place, 12 are partly in place and 7 are missing. The twin's SKU access (row 20) and MAA carrying our nonce (row 2) decide the rest of Phase 5. Rows 5, 6 and 20 need your accounts, so they can't be done from here.

| # | Dependency | Needed for | Status | Already there | Missing / action |
| --- | --- | --- | --- | --- | --- |
| 1 | Azure quota for `Standard_NCC40ads_H100_v5` (`StandardNCCads2023Family` ≥ 40 vCPUs) in eastus2 | Phase 5: confidential H100 | In place | Granted 2026-11-10 (support request 2611100050001296): 40 vCPUs, regional vCPUs 88; `infra/cc-pool` (validated) | - |
| 2 | Microsoft Azure Attestation token for the SEV-SNP CVM, carrying our nonce, from Azure's guest `AttestationClient` | Phase 5: CPU attestation | Partly in place | MAA collector (the sample app's `-a -n -o token` flags), verifier and policy, tested on the test platform; bring-up builds the client from source and checks the nonce is in the token | Confirm the client's build steps and flags on the CVM image, and the claim names (`x-ms-runtime.client-payload.nonce`, launch measurement) on the first token |
| 3 | AWS on-demand G and VT vCPU quota in us-east-1, and g6.2xlarge capacity | Phases 2–4 | In place | 384 vCPUs on-demand (2026-11-10); a test g6.2xlarge ran in us-east-1a; `infra/gpu-pool` (validated) | Spot quota is 0 (optional request); us-east-1b had no capacity that day, so set `az` per run |
| 4 | Linux host with KVM and Docker | 1.1 provisioning sim; SR Linux lab; L3 VNIs | Partly in place | Scripts, CI job `lab` on an Ubuntu runner, and `infra/lab-host` (AWS c5.metal, ADR 011) | Standard on-demand vCPU quota ≥ 96 in us-east-1; a first `make lab-host-up`. This Mac's Docker kernel crashes SR Linux and lacks VRF |
| 5 | GitHub repository with Actions and GHCR | 7.4 signed images; CI; reaper | Partly in place | `.github/workflows` (images, ci, reaper) | Create the remote and push; replace `REPLACE-OWNER` in the policy and Dockerfile |
| 6 | Cloud credentials for GitHub Actions (AWS OIDC role, Azure federated identity) | 8-hour reaper | Partly in place | `infra/reaper/reap.sh` (AWS and Azure passes), workflow; `infra/reaper/*.tf` creates both identities and the repo variables | `make reaper-up` (runbook); push the AWS/Azure workflow to `main`, which still runs the GCP pass |
| 7 | Sigstore public good (Fulcio, Rekor) and a pinned `trusted_root.json` | Image check in `alverify` | Partly in place | sigstore-go verification, tested with a virtual Sigstore | Run `cosign trusted-root create` (or TUF fetch) into `receipts/policy/prod/` |
| 8 | MAA instance and its pinned JWKS | CPU attestation | Partly in place | MAA token verifier; `maa_url` defaults to the shared eastus2 instance | Pin `<maa_url>/certs` in `receipts/policy/prod/` |
| 9 | NVIDIA NRAS and its JWKS; NVAT (`nvattest`) install | GPU attestation | Partly in place | NRAS EAT verifier; NVAT collector | Confirm NVAT package, CLI flags and JSON fields on Azure; pin NRAS JWKS; record VBIOS |
| 10 | CC-capable NVIDIA driver and `nvidia-smi conf-compute` flags | 7.2 bring-up | Partly in place | Azure SEV-SNP bring-up script with checks | Confirm package names and flags for the pinned driver branch on the NCC H100 v5 image |
| 11 | S3 buckets and the nodes' instance role | Durable tier (5.1) | Partly in place | `infra/gpu-pool/storage.tf` (validated); checkpoint code tested against an S3 API server | `tofu apply`; confirm the vLLM init container reaches the role through IMDS (hop limit 2) |
| 12 | Training dataset (FineWeb-Edu sample-10BT, ODC-By 1.0) and GPT-2 tokenizer | 4.1 training data | Missing | `prepare.py` (tokenise, shard, hash, upload) | Download \~2B tokens; confirm the licence fits your use |
| 13 | Open 7–8B model for serving numbers | 4.2 realistic serving | Missing | vLLM manifest takes any model prefix | Pick and upload weights (assumed Qwen2.5-7B-Instruct, Apache 2.0, not gated) |
| 14 | Incident destination (Alertmanager webhook, or GitHub token + repo) | healthd incidents, alerts | Missing | Alertmanager and GitHub-issue sinks, tested | `AL_ALERT_WEBHOOK_URL`, or `GITHUB_TOKEN` + repo |
| 15 | Cluster secrets (munge key, Slurm JWT key, the Grafana password) | `make up POOL=gpu` | Partly in place | Ansible reads them from env | Generate and export before `make up` (runbook, Phase 2) |
| 16 | SSH key pair and admin CIDR | All VMs | Partly in place | Variables in every pool | Your key path and IP in `terraform.tfvars` |
| 17 | RDMA between GPU nodes (EFA on AWS) | 2.2 RDMA row | Missing | RDMA path in the NCCL sbatch (InfiniBand/RoCE verbs); skips cleanly without it | g6.2xlarge has no EFA. EFA starts at g6.8xlarge (25 Gbps, 4× the vCPUs) and also needs the aws-ofi-nccl plugin and an `efa` transport in the sbatch (not written). Or publish TCP only (spec risk) |
| 18 | Upstream versions in `versions.env` | Every phase | Partly in place | All pins in one file | Re-check each pin; one pin (MinIO) was already wrong, and the GPU Operator pin is end of support |
| 19 | Tinkerbell stack chart, sushy-tools, Ubuntu cloud image | 1.1 provisioning | Partly in place | Values, template, hardware objects | Validate chart version and values on the KVM host |
| 20 | Azure region/SKU access to `Standard_NC40ads_H100_v5` (the non-CC twin) and its quota | 7.7 overhead benchmark | Missing | `create_twin` in `infra/cc-pool` | `NotAvailableForSubscription` in eastus2, centralus and westeurope (2026-11-10); file a region/SKU access request, then `StandardNCadsH100v5Family` ≥ 40 |
| 21 | EC2 instance topology for the GPU instance type | Topology-aware placement (ADR 007) | Missing | Cluster placement group on on-demand runs; topology role reads `DescribeInstanceTopology` | Not offered for g6.2xlarge (empty result on a running instance); only a larger type would give position data |

## Assumptions

I made 28 assumptions where the spec left a choice open or a vendor detail could not be checked from here. The riskiest are rows 13–16 and 22, attestation tool flags and claim names that only a real confidential VM can confirm, and row 23, Kueue's topology API in the pinned version.

| # | Area | Assumption | Where | How to confirm |
| --- | --- | --- | --- | --- |
| 1 | Clouds | GPU pool on AWS `g6.2xlarge` in us-east-1, one zone per run; confidential pool on Azure `Standard_NCC40ads_H100_v5` in eastus2 zone 1 (ADR 010) | `infra/*/variables.tf` | Quota and capacity at apply time |
| 2 | Versions | Every pin in `versions.env` was chosen on 2026-11-07, partly from memory; not all were checked upstream | `versions.env` | Re-check each pin before spending (spec ground rule) |
| 3 | Dataset | FineWeb-Edu sample-10BT (ODC-By 1.0), 2B tokens prepared, GPT-2 tokenizer | `workloads/train/prepare.py` | Licence review |
| 4 | Token budget | 800M tokens ≈ 1.5 h on 4 × L4 at \~20% MFU | `configs/gpt124m.yaml` | Re-size after the 1-GPU baseline |
| 5 | MFU peak | L4 dense bf16 = 121 TFLOPS (242 with sparsity) | `configs/gpt124m.yaml` | NVIDIA L4 datasheet |
| 6 | Serving model | Qwen2.5-7B-Instruct (Apache 2.0, not gated) for the 7–8B serving numbers | Not yet in config; set MODEL\_PREFIX in the vLLM ConfigMap | Your choice of model |
| 7 | Pools | GPU nodes 1–3 start in Slurm and node 4 in Kubernetes; Slurm features are `pool_slurm` / `pool_k8s` because Slurm feature names cannot contain "=" | `ansible/group_vars/gpu.yml`, `pool-move.sh` | - |
| 8 | Drain budget | "1 node or 25%" read as max(1, 25% of fleet) distinct nodes per rolling hour; raised to 8 for the goodput experiment | `healthd` options, `chaos/experiment.yaml` | Your reading of the spec |
| 9 | Burn-in baselines | Placeholders taken from public specs until the Phase 2 medians exist | `fleet/burnin/baselines.yaml` (status: placeholder) | Replace with measured medians |
| 10 | Checkpoints | `sync_durable` = local write + blocking upload to S3; FSDP uses `direct_durable` saves; restart time R = 180 s until measured | `ckpt.py`, `storage/interval.py` | Phase 4 measurements |
| 11 | Real-fleet MTBF | \~264 days per node, from Llama 3's 419 interruptions in 54 days on 16,384 GPUs | `storage/interval.py` | Your fleet data |
| 12 | Goodput experiment | MTBF 20 min, fault mix weights, operator delay 15 min (arm A), simulated repair after 2 min, injected GPU fault also kills the rank | `chaos/experiment.yaml`, `chaos/inject.sh` | Review before running |
| 13 | MAA token | `AttestationClient -a <MAA URL> -n <nonce> -o token` prints the JWT; MAA echoes the nonce in `x-ms-runtime.client-payload.nonce`; SEV-SNP claims sit under `x-ms-isolation-tee` (`x-ms-attestation-type=sevsnpvm`, `x-ms-compliance-status`, `x-ms-sevsnpvm-launchmeasurement`, `x-ms-sevsnpvm-is-debuggable`) | `evidence.go`, `policy.go`, prod policy | First CC VM (`bringup-maa-claims.json`) |
| 14 | NVAT output | `nvattest attest ... --nonce --format json` returns `evidences`, `detached_eat`, `driver_version`, `vbios_version` | `evidence.go` (`parseNVAT`) | Pinned NVAT release docs and a real run |
| 15 | NRAS claims | `x-nvidia-overall-att-result`, `eat_nonce`, `measres`, `dbgstat`, driver and VBIOS claims; CC mode implied by a successful CC attestation (no claim checked) | `receipts/verify/policy.go` | NRAS token from a real run |
| 16 | CC bring-up | Azure turns GPU CC mode on for NCC H100 v5 and the guest only sets the ready state; `nvidia-smi conf-compute -f`, `-srs 1`, `-grs` flags; driver package names | `cc-bringup.sh` | Azure confidential GPU guide on the VM |
| 17 | Nonce encoding | Raw 32-byte hashes concatenated with the key's DER SPKI, attested after the job | `receipt.go`, ADR 003 | - |
| 18 | Image proof | The receipt carries the image's SLSA provenance bundle (GitHub `attest-build-provenance`), checked against `images.yml@refs/heads/main` | `.github/workflows/images.yml`, prod policy | First signed build |
| 19 | Lab hosts | `nicolaka/netshoot:v0.14` hosts; SR Linux `ixrd2l` type for 49/50 uplink ports | `fabric.clab.yml` | Linux-host run |
| 20 | On-demand | Both Azure VMs and every AWS VM run on-demand (AWS Spot quota is 0; Azure NCC is on-demand) | `infra/*` | - |
| 21 | Quota names | `StandardNCCads2023Family` covers `Standard_NCC40ads_H100_v5`; `StandardNCadsH100v5Family` covers the twin | runbook, Phase 1 | `az vm list-usage --location eastus2` (confirmed for the first) |
| 22 | GPU tool on Azure | NVIDIA's `nvattest` with NRAS works on Azure's NCC H100 v5 | `evidence.go` | First CC VM |
| 23 | Kueue topology API | Kueue v0.14.1 accepts a `Topology` (applied at the CRD's storage version), `spec.topologyName` on a v1beta1 ResourceFlavor, and the `podset-unconstrained-topology` and `podset-preferred-topology` pod annotations | `sched/k8s/kueue/`, role `k8s_addons` | First `make up POOL=gpu`: vLLM and the quota test are admitted, `tas-example.yaml` is placed |
| 24 | EC2 position | `DescribeInstanceTopology` lists `NetworkNodes` top layer first; the last two map to block and sub-block | `fleet/topology` | Any covered instance type (not g6.2xlarge) |
| 25 | Rail NIC names | Secondary NICs are `ens6` and up on EC2 Ubuntu images (the primary is often `ens5`) | `fleet/healthd/rules.yaml` | `ip link` on a multi-NIC machine |
| 26 | Slurm topology | `topology/tree`, one switch per sub-block and per block; configless slurmd receives `topology.conf` from slurmctld | `sched/slurm/slurm.conf.j2`, role `topology` | `scontrol show topology` on the pool |
| 27 | S3 from pods | The vLLM init container gets the node role's credentials through IMDSv2 (hop limit 2) | `infra/gpu-pool`, `sched/k8s/vllm/vllm.yaml` | First weight sync on the k8s node |
| 28 | Operating system | Ubuntu 22.04 on every VM: Canonical's jammy AMIs on AWS, the `22_04-lts-cvm` confidential image on Azure (ADR 009) | `infra/*`, `versions.env` | `az vm image list --publisher Canonical --offer 0001-com-ubuntu-confidential-vm-jammy --all` |
