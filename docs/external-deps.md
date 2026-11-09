# External dependencies and assumptions

Snapshot of 2026-11-08.

## External dependencies

22 external dependencies stand between the repo and measured results: none are fully in place, 11 are partly in place and 11 are missing. Everything targets Google Cloud (ADR 006). The confidential A3 quota (row 1) and whether Google Cloud Attestation covers TDX on A3 (row 2) decide Phase 5. Rows 1, 3, 5 and 21 need your accounts, so they can't be done from here.

| # | Dependency | Needed for | Status | Already there | Missing / action |
| --- | --- | --- | --- | --- | --- |
| 1 | GCP quota for Confidential A3 High: preemptible H100 GPUs (≥ 2: confidential VM + twin) and A3 CPUs, us-central1 | Phase 5: confidential H100 | Missing | `infra/cc-pool` (validated), budget in code | Request in the GPU pool's project; confidential A3 is Spot or flex-start only (no on-demand, no reservations) |
| 2 | Google Cloud Attestation issuing TDX tokens on `a3-highgpu-1g` | Phase 5: CPU attestation | Missing | GCA collector, verifier and policy, tested on the test platform; raw TDX quote also embedded | Google documents GCA's TDX support for C3 only. Bring-up fails fast if refused; then verify the raw quote (v2) with Intel DCAP or Intel Trust Authority; there is no second cloud for confidential receipts |
| 3 | GCP project, billing, L4 quota (≥ 4 `NVIDIA_L4_GPUS` + G2 CPUs, us-central1) | Phases 2–4 | Missing | `infra/gpu-pool` (validated), budget in code | Project id, billing account, quota request |
| 4 | Linux host with KVM and Docker | 1.1 provisioning sim; SR Linux lab; L3 VNIs | Missing | Scripts, and CI job `lab` on an Ubuntu runner | A Linux workstation or VM; this Mac's Docker kernel crashes SR Linux and lacks VRF |
| 5 | GitHub repository with Actions and GHCR | 7.4 signed images; CI; reaper | Partly in place | `.github/workflows` (images, ci, reaper); local git, 10 commits | Create the remote and push; replace `REPLACE-OWNER` in the policy and Dockerfile |
| 6 | Cloud credentials for GitHub Actions (GCP Workload Identity Federation) | 8-hour reaper | Missing | `infra/reaper/reap.sh`, workflow | Create the GCP identity; set repo variables |
| 7 | Sigstore public good (Fulcio, Rekor) and a pinned `trusted_root.json` | Image check in `alverify` | Partly in place | sigstore-go verification, tested with a virtual Sigstore | Run `cosign trusted-root create` (or TUF fetch) into `receipts/policy/prod/` |
| 8 | Confidential Computing API and the pinned GCA JWKS | CPU attestation | Partly in place | GCA token verifier; OpenTofu enables the API | Pin the keys at the `jwks_uri` of https://confidentialcomputing.googleapis.com/.well-known/openid-configuration; confirm claims on the first token |
| 9 | NVIDIA NRAS and its JWKS; NVAT (`nvattest`) install | GPU attestation | Partly in place | NRAS EAT verifier; NVAT collector | Confirm NVAT package, CLI flags and JSON fields on GCP; pin NRAS JWKS; record VBIOS |
| 10 | go-tpm-tools `gotpm` v0.4.11 on the VM | GCA token carrying our nonce | Partly in place | Collector using flags read from `cmd/token.go`; bring-up builds it with `go install` | Confirm the pinned tag builds on the VM and the token lands in `--output` |
| 11 | CC-capable NVIDIA driver and `nvidia-smi conf-compute` flags | 7.2 bring-up | Partly in place | GCP TDX bring-up script with checks | Confirm package names and flags for the pinned driver branch on GCP |
| 12 | Cloud Storage buckets and an HMAC key for the nodes' service account | Durable tier (5.1) | Partly in place | `infra/gpu-pool/storage.tf` (validated); checkpoint code tested against an S3 API server | `tofu apply`; confirm Google's S3 interoperability API accepts our boto3, s3fs and aws-cli calls (ADR 008) |
| 13 | Training dataset (FineWeb-Edu sample-10BT, ODC-By 1.0) and GPT-2 tokenizer | 4.1 training data | Missing | `prepare.py` (tokenise, shard, hash, upload) | Download \~2B tokens; confirm the licence fits your use |
| 14 | Open 7–8B model for serving numbers | 4.2 realistic serving | Missing | vLLM manifest takes any model prefix | Pick and upload weights (assumed Qwen2.5-7B-Instruct, Apache 2.0, not gated) |
| 15 | Incident destination (Alertmanager webhook, or GitHub token + repo) | healthd incidents, alerts | Missing | Alertmanager and GitHub-issue sinks, tested | `AL_ALERT_WEBHOOK_URL`, or `GITHUB_TOKEN` + repo |
| 16 | Cluster secrets (munge key, Slurm JWT key, the object-store HMAC key and the Grafana password) | `make up POOL=gpu` | Partly in place | Ansible reads them from env | Generate and export before `make up` (runbook, Phase 2) |
| 17 | SSH key pair and admin CIDR | All VMs | Partly in place | Variables in every pool | Your key path and IP in `terraform.tfvars` |
| 18 | A provider with RDMA on small GPU instances | 2.2 RDMA row | Missing | RDMA path in the NCCL sbatch; skips cleanly without it | Choose a provider, or publish TCP only (spec risk) |
| 19 | Upstream versions in `versions.env` | Every phase | Partly in place | All pins in one file | Re-check each pin; one pin (MinIO) was already wrong, and the GPU Operator pin is end of support |
| 20 | Tinkerbell stack chart, sushy-tools, Ubuntu cloud image | 1.1 provisioning | Partly in place | Values, template, hardware objects | Validate chart version and values on the KVM host |
| 21 | AWS account (second choice) | GPU pool fallback, only if GCP L4 quota is refused | Missing | Nothing built (ADR 006) | `g6.2xlarge` (1x L4) is the closest match; AWS sells no confidential GPU |
| 22 | GCE capacity for 4 `g2-standard-8` in one compact placement policy | Topology-aware placement on timed runs (ADR 007) | Missing | `compact_placement` in `infra/gpu-pool` (validated), applied to on-demand runs only | Shown by the first on-demand apply; set `compact_placement = false` if capacity is refused |

## Assumptions

I made 29 assumptions where the spec left a choice open or a vendor detail could not be checked from here. The riskiest are rows 13–16 and 23, attestation tool flags and claim names that only a real confidential VM can confirm, and row 24, Kueue's topology API in the pinned version.

| # | Area | Assumption | Where | How to confirm |
| --- | --- | --- | --- | --- |
| 1 | Clouds | Both pools on GCP in one project: GPU pool `g2-standard-8` and confidential pool `a3-highgpu-1g`, both in us-central1-a. AWS is second (GPU pool only; it sells no confidential GPU) | `infra/*/variables.tf`, ADR 006 | Quota and availability at request time |
| 2 | Versions | Every pin in `versions.env` was chosen on 2026-11-07, partly from memory; not all were checked upstream | `versions.env` | Re-check each pin before spending (spec ground rule) |
| 3 | Dataset | FineWeb-Edu sample-10BT (ODC-By 1.0), 2B tokens prepared, GPT-2 tokenizer | `workloads/train/prepare.py` | Licence review |
| 4 | Token budget | 800M tokens ≈ 1.5 h on 4 × L4 at \~20% MFU | `configs/gpt124m.yaml` | Re-size after the 1-GPU baseline |
| 5 | MFU peak | L4 dense bf16 = 121 TFLOPS (242 with sparsity) | `configs/gpt124m.yaml` | NVIDIA L4 datasheet |
| 6 | Serving model | Qwen2.5-7B-Instruct (Apache 2.0, not gated) for the 7–8B serving numbers | Not yet in config; set MODEL\_PREFIX in the vLLM ConfigMap | Your choice of model |
| 7 | Pools | GPU nodes 1–3 start in Slurm and node 4 in Kubernetes; Slurm features are `pool_slurm` / `pool_k8s` because Slurm feature names cannot contain "=" | `ansible/group_vars/gpu.yml`, `pool-move.sh` | - |
| 8 | Drain budget | "1 node or 25%" read as max(1, 25% of fleet) distinct nodes per rolling hour; raised to 8 for the goodput experiment | `healthd` options, `chaos/experiment.yaml` | Your reading of the spec |
| 9 | Burn-in baselines | Placeholders taken from public specs until the Phase 2 medians exist | `fleet/burnin/baselines.yaml` (status: placeholder) | Replace with measured medians |
| 10 | Checkpoints | `sync_durable` = local write + blocking upload to Cloud Storage; FSDP uses `direct_durable` saves; restart time R = 180 s until measured | `ckpt.py`, `storage/interval.py` | Phase 4 measurements |
| 11 | Real-fleet MTBF | \~264 days per node, from Llama 3's 419 interruptions in 54 days on 16,384 GPUs | `storage/interval.py` | Your fleet data |
| 12 | Goodput experiment | MTBF 20 min, fault mix weights, operator delay 15 min (arm A), simulated repair after 2 min, injected GPU fault also kills the rank | `chaos/experiment.yaml`, `chaos/inject.sh` | Review before running |
| 13 | GCA token | `gotpm token --tee-technology tdx --custom-nonce <nonce> --audience <aud> --output <file>` (flags read from the source, not run); GCA echoes the nonce in `eat_nonce`; plain-CVM tokens carry `hwmodel=GCP_INTEL_TDX`, `secboot`, `dbgstat` and `submods.gce.project_id` | `evidence.go`, `policy.go`, prod policy | First CC VM (`bringup-gca-claims.json`) |
| 14 | NVAT output | `nvattest attest ... --nonce --format json` returns `evidences`, `detached_eat`, `driver_version`, `vbios_version` | `evidence.go` (`parseNVAT`) | Pinned NVAT release docs and a real run |
| 15 | NRAS claims | `x-nvidia-overall-att-result`, `eat_nonce`, `measres`, `dbgstat`, driver and VBIOS claims; CC mode implied by a successful CC attestation (no claim checked) | `receipts/verify/policy.go` | NRAS token from a real run |
| 16 | CC bring-up | GCP turns GPU CC mode on for confidential A3 and the guest only sets the ready state; `nvidia-smi conf-compute -f`, `-srs 1`, `-grs` flags; driver package names; `tdx_guest` module from `linux-modules-extra-gcp` | `cc-bringup.sh` | GCP confidential GPU guide on the VM |
| 17 | Nonce encoding | Raw 32-byte hashes concatenated with the key's DER SPKI, attested after the job | `receipt.go`, ADR 003 | - |
| 18 | Image proof | The receipt carries the image's SLSA provenance bundle (GitHub `attest-build-provenance`), checked against `images.yml@refs/heads/main` | `.github/workflows/images.yml`, prod policy | First signed build |
| 19 | Lab hosts | `nicolaka/netshoot:v0.14` hosts; SR Linux `ixrd2l` type for 49/50 uplink ports | `fabric.clab.yml` | Linux-host run |
| 20 | Repo | Local git at `~/attestland`; GitHub owner is a `REPLACE-OWNER` placeholder | policy, Dockerfile | Your GitHub org |
| 21 | Spot | Both the confidential VM and its twin run as Spot; a preempted benchmark repeat is rerun; flex-start (a MIG) is the manual fallback | `infra/cc-pool`, runbook | First Phase 5 session |
| 22 | Quota names | `PREEMPTIBLE_NVIDIA_H100_GPUS` plus the A3 CPU quota cover Spot confidential A3 | runbook, Phase 1 | Console quota page |
| 23 | GPU tool on GCP | NVIDIA's `nvattest` with NRAS works on GCP. Google also documents its own `nvattest` (go-nvattest-tools) and a known measurement mismatch at index 9, fixed by a full stop and start | `evidence.go` | First CC VM |
| 24 | Kueue topology API | Kueue v0.14.1 accepts a `Topology` (applied at the CRD's storage version), `spec.topologyName` on a v1beta1 ResourceFlavor, and the `podset-unconstrained-topology` and `podset-preferred-topology` pod annotations | `sched/k8s/kueue/`, role `k8s_addons` | First `make up POOL=gpu`: vLLM and the quota test are admitted, `tas-example.yaml` is placed |
| 25 | GCE position | G2 VMs in a compact placement policy expose block and sub-block in `physical_host_topology`; Spot VMs stay out of the policy because that combination is not documented | `infra/gpu-pool`, `fleet/topology` | Read the metadata key on the first on-demand node |
| 26 | Rail NIC names | Secondary NICs are `ens5` and up, or `eth1` and up, on GCE Ubuntu images | `fleet/healthd/rules.yaml` | `ip link` on a multi-NIC machine |
| 27 | Slurm topology | `topology/tree`, one switch per sub-block and per block; configless slurmd receives `topology.conf` from slurmctld | `sched/slurm/slurm.conf.j2`, role `topology` | `scontrol show topology` on the pool |
| 28 | Cloud Storage S3 API | Google's S3 interoperability endpoint accepts multipart uploads, versioning calls and requests signed with region `auto` when the clients send checksums only when required; DCP's s3fs writer works the same way | `workloads/train/ckpt.py`, `sched/k8s/vllm/vllm.yaml` | First checkpoint save and weight sync on a GPU node |
| 29 | Operating system | Ubuntu 22.04 on every VM; Google lists it for confidential A3 High, 24.04 not yet (ADR 009) | `infra/*/variables.tf`, `versions.env` | `gcloud compute images list` filtered for TDX_CAPABLE |
