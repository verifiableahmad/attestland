# Runbook: phase by phase

Every step lists its command and the result file it must leave behind. Do
the Phase 1 quota requests first; they are the longest lead-time items.

## Phase 1 - Local lab (no money)

1. File the GCP quota requests (see docs/external-deps.md, items 1 and 3), all in
   the one project: preemptible H100 (`PREEMPTIBLE_NVIDIA_H100_GPUS`) ≥ 2 in
   us-central1 for the confidential VM and its twin; L4 (`NVIDIA_L4_GPUS`) ≥ 4
   in us-central1.
2. `make test` - Go and Python tests.
3. Linux host: `make lab-up && make lab-test` (SR Linux). Laptop:
   `make lab-up LAB_NOS=frr && make lab-test LAB_NOS=frr`.
   Leaves `bench/results/net/convergence-*.json`.
4. Linux/KVM host: `make provision N=3` → `bench/results/fleet/provision-*.json`.
5. Read `network/design-note.md`; rerun `network/sizing.py` if inputs change.

## Phase 2 - GPU cluster (GCP, ~60 GPU-hours budgeted)

1. `cp infra/gpu-pool/terraform.tfvars.example infra/gpu-pool/terraform.tfvars`, fill it.
2. Export secrets: `AL_MUNGE_KEY_B64`, `AL_SLURM_JWT_KEY_B64`,
   `AL_GRAFANA_PASSWORD`, optional
   `AL_ALERT_WEBHOOK_URL` (generate keys with `openssl rand -base64 1024` / `32`).
3. Optional, saves paid time: `make image POOL=gpu` (Packer), then set `gpu_image`.
4. `make up POOL=gpu` (spot=true for development). Check `sinfo`, `kubectl get nodes`, Grafana on :3000.
5. Data: `python workloads/train/prepare.py --source hf:HuggingFaceFW/fineweb-edu:sample-10BT --tokens 2_000_000_000 --out /scratch/data/fineweb-edu-2b --upload s3://datasets/fineweb-edu-2b`, then sync to each node's /scratch.
6. Set `spot = false`, re-apply, then the timed runs:
   - `make nccl` → `bench/results/net/nccl-*.json`
   - `make train-sweep` → `bench/results/train/scale-*.json`, `scaling-summary-*.json`
   - export a checkpoint with `workloads/train/export_hf.py`, upload to `s3://models/al-gpt-124m-hf`,
     `make pool-move NODE=al-gpu-04 TO=k8s`, `make serve-bench` → `bench/results/serve/*.json`
   - Kueue quota check: `kubectl create -f sched/k8s/kueue/quota-test.yaml` three times; extras stay `Pending`.
   - Topology (ADR 007, on-demand runs use compact placement): `scontrol show topology` and
     `kubectl get nodes -L fleet.attestland/topology-block,fleet.attestland/topology-subblock`.
     `unknown` means GCE exposed no position (Spot run, or not in the policy).
     `kubectl create -f sched/k8s/kueue/tas-example.yaml`: both pods should share a sub-block.
7. Update `fleet/burnin/baselines.yaml` with the measured medians (status: measured).
8. `make down POOL=gpu`.

## GCP prerequisites (org policies and credentials)

Hit on a first `make infra-up` in an org with default-restrictive policies. Org-policy
changes need `roles/orgpolicy.policyAdmin`; they can take a minute or two to propagate.
Replace `attestland` with your project id.

1. SSH key: `terraform.tfvars` defaults to `~/.ssh/id_ed25519.pub`. Either generate it
   (`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""`) or set
   `ssh_public_key_path = "~/.ssh/google_compute_engine.pub"`.
2. ADC quota project (Billing Budgets API returns 403 without it):
   `gcloud auth application-default set-quota-project attestland`
3. HMAC key for the S3 interop API (ADR 008) fails with
   `constraints/iam.disableServiceAccountKeyCreation`:
   `gcloud resource-manager org-policies disable-enforce iam.disableServiceAccountKeyCreation --project=attestland`
4. External IPs fail with `constraints/compute.vmExternalIpAccess`. Inspect, then allow:
   ```
   gcloud resource-manager org-policies describe compute.vmExternalIpAccess --project=attestland --effective
   printf 'constraint: constraints/compute.vmExternalIpAccess\nlistPolicy:\n  allValues: ALLOW\n' > policy.yaml
   gcloud resource-manager org-policies set-policy policy.yaml --project=attestland
   ```
   `org-policies delete compute.vmExternalIpAccess --project=attestland` only re-inherits
   from the parent, so it does nothing if the restriction is set higher up.
5. Enable APIs (Terraform fails with `SERVICE_DISABLED` naming any that are missing):
   `gcloud services enable compute.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com billingbudgets.googleapis.com --project=attestland`
   (list what is on with `gcloud services list --enabled --project=attestland`).
6. If a command returns `PERMISSION_DENIED`: `gcloud organizations list`,
   `gcloud projects get-ancestors attestland` (find where the policy is set), and ask the
   org admin for `roles/orgpolicy.policyAdmin` or a project-level exception.

## Phase 3 - Fleet automation

1. `make up POOL=gpu`; `make burnin` → per-node reports, `bench/results/burnin/`.
   Check: a node with a deliberately wrong driver fails only the software check.
2. healthd is deployed by `make up`; start in dry-run (`healthd_dry_run: true`), then enforce.
3. Live fault: start training, then `python chaos/measure_drain.py --node al-gpu-02`
   → `bench/results/fleet/drain-xid79-*.json`.

## Phase 4 - Storage and reliability

1. `make ckpt-bench` → `bench/results/ckpt/ckpt-*.json` (124M model, 10 GB, 50 GB).
2. `make ckpt-interval` → writes `checkpoint.interval_s` into `chaos/experiment.yaml`.
3. Deploy healthd with `--budget-min=8` for the experiment; `make goodput` (~10–12 h of
   GPU time: baseline + 2 arms × 3 repeats) → `bench/results/goodput/*.json`.
4. `make plots`; `make down POOL=gpu`.

## Phase 5 - Confidential receipts (GCP)

1. Push to `main` so `.github/workflows/images.yml` builds, signs and attests
   `vllm-receipts`; note its `@sha256:` reference.
2. `cp infra/cc-pool/terraform.tfvars.example infra/cc-pool/terraform.tfvars`, fill it
   (same project as the GPU pool). Confidential A3 High is Spot only here; if
   Spot capacity is short, create it by hand with flex-start (a MIG with
   `--provisioning-model=FLEX_START --confidential-compute-type=TDX`).
3. `make up POOL=cc` (enables the Confidential Computing API; bring-up runs
   `receipts/bringup/cc-bringup.sh`). Check `/var/lib/fleet/bringup-gca-claims.json`:
   `hwmodel` must be `GCP_INTEL_TDX`. If GCA refuses TDX on A3, stop and see ADR 006.
4. Fill `receipts/policy/prod/`: GCA JWKS (the `jwks_uri` in
   https://confidentialcomputing.googleapis.com/.well-known/openid-configuration),
   NRAS JWKS, Sigstore trusted root, your project id, and the VBIOS from bring-up.
5. On the VM: `receipts/bringup/run-job.sh job-001 receipts/bringup/sample-requests.jsonl /models/current out/`.
6. Laptop: `bin/alverify out/receipt.json --policy receipts/policy/prod/policy.yaml --outputs out/outputs.jsonl`.
7. Negative tests on the real receipt: edit a byte, swap the digest, etc.; each must fail.
8. `create_twin = true`, `make up POOL=cc`, `make cc-overhead` → `bench/results/cc/overhead-*.json`.
9. `make down POOL=cc`.
