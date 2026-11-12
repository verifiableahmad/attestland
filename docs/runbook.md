# Runbook: phase by phase

Every step lists its command and the result file it must leave behind. Do
the Phase 1 quota requests first; they are the longest lead-time items.

## Phase 1 - Local lab (no money)

1. Quotas (see docs/external-deps.md, items 1 and 3). AWS: on-demand G and VT
   vCPUs ≥ 40 in us-east-1 (4 × g6.2xlarge plus room); optional G Spot vCPUs for
   cheaper development. Azure: `StandardNCCads2023Family` ≥ 40 vCPUs in eastus2
   (granted 2026-11-10) and, for the overhead twin, region/SKU access to
   `Standard_NC40ads_H100_v5` plus `StandardNCadsH100v5Family` ≥ 40. GPU
   quota above 0 usually needs a portal support request; the free support plan
   cannot open one from the CLI.
2. `make test` - Go and Python tests.
3. x86 Linux host: `make lab-up && make lab-test` (SONiC, ADR 012; the first run
   downloads docker-sonic-vs and prints its sha256: pin it as `SONIC_VS_SHA256`
   in `versions.env`). No Linux machine:
   `make lab-host-up` creates an AWS c5.metal (ADR 011, ~$4.08/h, needs
   standard on-demand vCPU quota ≥ 96), then `make lab-host-ssh` and run steps
   3 and 4 in `~/attestland`; `make lab-host-down` when done. Laptop (FRR, no
   type-5 test): `make lab-up LAB_NOS=frr && make lab-test LAB_NOS=frr`.
   Leaves `bench/results/net/convergence-*.json`.
4. Linux/KVM host: `make provision N=3` → `bench/results/fleet/provision-*.json`.
5. Read `network/design-note.md`; rerun `network/sizing.py` if inputs change.

## AWS and Azure prerequisites (credentials and accounts)

1. SSH key: both pools default to `~/.ssh/id_ed25519.pub`
   (`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""`). Cloud-init (AWS) and the
   VM resource (Azure) create the `al` user with it.
2. AWS credentials: an IAM user or SSO role with EC2, VPC, S3, IAM (roles and
   instance profiles) and Budgets rights; not the account root user.
   `aws sts get-caller-identity` shows which identity the CLI uses. To create an
   IAM user `al-operator` with those rights, run this once with the root (or an
   admin) credentials:

   ```sh
   aws iam create-user --user-name al-operator --tags Key=project,Value=attestland
   # EC2 covers VPC; IAM covers roles, instance profiles and the reaper's OIDC
   # provider; CloudTrail read is for diagnosing RunInstances errors.
   for p in AmazonEC2FullAccess AmazonS3FullAccess IAMFullAccess AWSCloudTrail_ReadOnlyAccess; do
     aws iam attach-user-policy --user-name al-operator \
       --policy-arn "arn:aws:iam::aws:policy/$p"
   done
   aws iam put-user-policy --user-name al-operator --policy-name budgets \
     --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"budgets:*","Resource":"*"}]}'
   aws iam create-access-key --user-name al-operator \
     --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text |
     { read -r id secret
       aws configure set aws_access_key_id "$id" --profile attestland
       aws configure set aws_secret_access_key "$secret" --profile attestland; }
   aws configure set region us-east-1 --profile attestland
   export AWS_PROFILE=attestland   # add to your shell profile
   aws sts get-caller-identity     # Arn must end in :user/al-operator
   ```

   Then delete the root user's access key (console → Security credentials).
3. AWS budget: activate `project` as a cost allocation tag (Billing → Cost
   allocation tags) or the budget's tag filter matches nothing.
4. Azure: `az login --use-device-code`. If it finds no subscriptions, the
   subscription is in another tenant: `az login --tenant <tenant>`. The portal
   needs the tenant in the URL for the same reason
   (`https://portal.azure.com/#@<tenant domain>/...`).
5. Azure providers: `az provider register -n Microsoft.Compute` (and
   `Microsoft.Consumption` for the budget) once per subscription.
6. Reaper (once, after 2 and 4): `cp infra/reaper/terraform.tfvars.example
   infra/reaper/terraform.tfvars`, fill it, then `make reaper-up`. It creates the
   GitHub OIDC provider and an IAM role `al-reaper` (describe, and terminate
   only `project=attestland` instances without `keep=true`); an Entra app
   `al-reaper` with a federated credential for `main` of this repository and a
   list/delete-VM role on the subscription; and writes `AWS_REGION`,
   `AWS_REAPER_ROLE_ARN`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID` to the repository's Actions variables (with
   `gh auth token`, or `GITHUB_TOKEN` if set). Needs IAM rights on AWS, Owner (or
   User Access Administrator) on the subscription, and the right to register
   apps in the tenant. Check: `gh workflow run reaper -f dry_run=1`, then the
   run log shows both passes listing instead of skipping.

## Phase 2 - GPU cluster (AWS, ~60 GPU-hours budgeted)

1. `cp infra/gpu-pool/terraform.tfvars.example infra/gpu-pool/terraform.tfvars`, fill it.
2. Export secrets: `AL_MUNGE_KEY_B64`, `AL_SLURM_JWT_KEY_B64`,
   `AL_GRAFANA_PASSWORD`, `AL_SLURMDBD_DB_PASSWORD`, optional
   `AL_ALERT_WEBHOOK_URL` (Alertmanager's webhook format; unset means log-only).
   They are generated locally, not fetched. Write them once to the git-ignored
   `.env` and source it in every shell that runs `make up`; new keys on a
   re-apply restart munge and drop running jobs.

   ```sh
   [ -f .env ] || ( umask 077; {
     echo "export AL_MUNGE_KEY_B64=$(openssl rand -base64 1024 | tr -d '\n')"
     echo "export AL_SLURM_JWT_KEY_B64=$(openssl rand -base64 32)"
     echo "export AL_GRAFANA_PASSWORD=$(openssl rand -hex 16)"
     echo "export AL_SLURMDBD_DB_PASSWORD=$(openssl rand -hex 16)"
     echo "# export AL_ALERT_WEBHOOK_URL=https://..."
   } > .env )
   . ./.env
   ```
3. `make up POOL=gpu` (on-demand; `spot = true` only once the account has G Spot quota).
   If EC2 returns `InsufficientInstanceCapacity`, set `az` to another zone and re-apply.
   Check `sinfo`, `kubectl get nodes`, Grafana on :3000.
4. Data: `python workloads/train/prepare.py --source hf:HuggingFaceFW/fineweb-edu:sample-10BT --tokens 2_000_000_000 --out /scratch/data/fineweb-edu-2b --upload s3://datasets/fineweb-edu-2b`, then sync to each node's /scratch.
5. With `spot = false` (the default), the timed runs:
   - `make nccl` → `bench/results/net/nccl-*.json`
   - `make train-sweep` → `bench/results/train/scale-*.json`, `scaling-summary-*.json`
   - export a checkpoint with `workloads/train/export_hf.py`, upload to `s3://models/al-gpt-124m-hf`,
     `make pool-move NODE=al-gpu-04 TO=k8s`, `make serve-bench` → `bench/results/serve/*.json`
   - Kueue quota check: `kubectl create -f sched/k8s/kueue/quota-test.yaml` three times; extras stay `Pending`.
   - Topology (ADR 007, on-demand runs use a cluster placement group): `scontrol show topology` and
     `kubectl get nodes -L fleet.attestland/topology-block,fleet.attestland/topology-subblock`.
     Expect `unknown` on g6.2xlarge: EC2 reports no topology for it (ADR 010).
     `kubectl create -f sched/k8s/kueue/tas-example.yaml` is admitted in the single group.
6. Update `fleet/burnin/baselines.yaml` with the measured medians (status: measured).
7. `make down POOL=gpu`.

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

## Phase 5 - Confidential receipts (Azure)

1. Push to `main` so `.github/workflows/images.yml` builds, signs and attests
   `vllm-receipts`; note its `@sha256:` reference.
2. `cp infra/cc-pool/terraform.tfvars.example infra/cc-pool/terraform.tfvars`, fill it
   (subscription id; `location`/`zone` must offer NCC40ads_H100_v5 to the subscription:
   `az vm list-skus --all --size Standard_NCC40ads_H100_v5 -o table`).
3. Copy the model to the VM once it exists: `aws s3 sync s3://<prefix>-models/al-gpt-124m-hf ./m`
   on the laptop, then `rsync` to `/models/current` on the VM.
4. `make up POOL=cc` (bring-up runs `receipts/bringup/cc-bringup.sh`). Check
   `/var/lib/fleet/bringup-maa-claims.json`: `x-ms-attestation-type` must be
   `sevsnpvm`, and the nonce must appear under `x-ms-runtime`.
5. Fill `receipts/policy/prod/`: MAA JWKS (`<maa_url>/certs`), NRAS JWKS, Sigstore
   trusted root, the launch measurement and the VBIOS from bring-up.
6. On the VM: `receipts/bringup/run-job.sh job-001 receipts/bringup/sample-requests.jsonl /models/current out/`.
7. Laptop: `bin/alverify out/receipt.json --policy receipts/policy/prod/policy.yaml --outputs out/outputs.jsonl`.
8. Negative tests on the real receipt: edit a byte, swap the digest, etc.; each must fail.
9. Overhead benchmark, once the twin's SKU access is granted: `create_twin = true`,
   `make up POOL=cc`, `make cc-overhead` → `bench/results/cc/overhead-*.json`.
10. `make down POOL=cc`.
