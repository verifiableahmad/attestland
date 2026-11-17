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
   for p in AmazonEC2FullAccess AmazonS3FullAccess IAMFullAccess AWSCloudTrail_ReadOnlyAccess \
            ServiceQuotasReadOnlyAccess; do
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

   `make` uses `AWS_PROFILE=attestland` unless you set another. An existing
   `al-operator` from before 2026-11-17 also needs the quota check's read
   right (`make up` warns without it), then delete the root user's access key,
   with the profile that holds it:

   ```sh
   aws iam attach-user-policy --user-name al-operator --profile attestland \
     --policy-arn arn:aws:iam::aws:policy/ServiceQuotasReadOnlyAccess
   aws iam list-access-keys --profile root-user --query 'AccessKeyMetadata[].AccessKeyId' --output text
   aws iam delete-access-key --profile root-user --access-key-id <id from the line above>
   aws configure list-profiles   # then remove the root-user profile from ~/.aws/credentials
   ```
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

## Nebius GPU pool (alternative to AWS, ADR 013)

The GPU pool can run on Nebius instead of AWS. Every Phase 2-4 step is the same
with `CLOUD=nebius` added to the `make up`, `configure`, `down`, `control-ssh`
and `burnin` commands.

1. CLI and login (browser), then note the project and tenant IDs:

   ```sh
   curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh | bash
   nebius profile create
   nebius iam project list
   nebius iam tenant list
   ```
2. Quotas in eu-north1 (console, Quotas). The default tier has 2 regular VMs,
   4 disks and 0 non-GPU vCPUs, so request: regular VMs = gpu_nodes + 1,
   disks = gpu_nodes + 1, non-GPU vCPUs ≥ 4 (the control VM); for the default
   2 GPU nodes that is 3 VMs, 3 disks and 4 vCPUs. 8 L40S GPUs are there by
   default. Without the vCPU increase the control VM cannot start.
3. Check that the image family exists for both platforms:

   ```sh
   nebius compute image list-public --region eu-north1 | grep -B2 -A8 ubuntu24.04-driverless
   ```
4. Configure and create. Fill `project_id` and `tenant_id` from step 1 and
   `admin_cidrs` with your current public IP. `.env` holds the cluster secrets
   (Phase 2, step 2 creates it).

   ```sh
   cp infra/gpu-pool-nebius/terraform.tfvars.example infra/gpu-pool-nebius/terraform.tfvars
   curl -s https://checkip.amazonaws.com      # -> admin_cidrs = ["<ip>/32"]
   . ./.env
   make up POOL=gpu CLOUD=nebius
   make control-ssh CLOUD=nebius
   ```
5. No reaper covers Nebius yet: destroy the pool at the end of every session.

   ```sh
   make down POOL=gpu CLOUD=nebius
   nebius compute instance list --format jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.state}{"\n"}{end}'
   ```

## Phase 2 - GPU cluster (AWS, ~60 GPU-hours budgeted)

1. `cp infra/gpu-pool/terraform.tfvars.example infra/gpu-pool/terraform.tfvars`, fill it.
2. Export secrets: `AL_MUNGE_KEY_B64`, `AL_SLURM_JWT_KEY_B64`,
   `AL_GRAFANA_PASSWORD`, `AL_SLURMDBD_DB_PASSWORD`, optional
   `AL_ALERT_WEBHOOK_URL` (Alertmanager's webhook format; unset means log-only).
   They are generated locally, not fetched. Write them once to the git-ignored
   `.env` and source it in every shell that runs `make up`; new keys on a
   re-apply restart munge and drop running jobs. `make configure` stops at its
   first task if the munge or JWT key is missing.

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
3. Durable tier, once per account: the four buckets and the bucket for the GPU
   pool's OpenTofu state, in their own module (`infra/durable`), so `make down`
   never touches them. `import_buckets` adopts buckets an earlier `make up`
   made (on 2026-11-16: `datasets`, with the corpus). Then move the pool's
   local state into the state bucket, once.

   ```sh
   cp infra/durable/terraform.tfvars.example infra/durable/terraform.tfvars
   make durable-up                  # review the plan: imports, no destroys
   make state-migrate               # infra/gpu-pool/terraform.tfstate -> s3://<prefix>-tfstate
   ```
4. `make up POOL=gpu` (on-demand; `spot = true` only once the account has G Spot quota).
   It checks the G/VT vCPU quota first and refuses a plan that replaces a VM
   (a replaced `al-control` takes the results on it); `ALLOW_REPLACE=1` after
   `make results-pull` lets it through. If EC2 returns
   `InsufficientInstanceCapacity`, move only the GPU nodes: set `control_az` to
   the current zone, then `az` to another one, and re-apply.
   The control node is the login node: `make up` copies this working tree to
   `/opt/al/repo` there and shares it (read-only), `/opt/al/results`,
   `/scratch/logs` and `/scratch/runs` with the GPU nodes over NFS, so steps 5
   and 6 run on it and no step needs a shell on a GPU node. After a local edit,
   `make sync POOL=gpu` copies the tree again; it restarts nothing (`make
   configure` re-runs every role and restarts what changed).

   ```sh
   . ./.env && make up POOL=gpu
   make sync POOL=gpu               # after each local edit
   make control-ssh                 # shell in /opt/al/repo on the control node
   sinfo; kubectl get nodes         # Grafana: http://<control ip>:3000
   tail -f /scratch/logs/<job>-<id>.log
   ```
5. Data, on the control node: `make data` builds the corpus in the training
   container on one Slurm node, uploads it to `s3://<prefix>-datasets/fineweb-edu-2b`
   and copies it to the `/scratch` of every idle Slurm node. Run it again after
   `pool-move` or a node replacement; it then only copies what is missing.

   ```sh
   make data                        # NODES=n to choose the node count
   ```
6. With `spot = false` (the default), the timed runs, on the control node.
   Result files land in `/opt/al/results` there (`AL_RESULTS_DIR`), each NCCL
   one with its raw log beside it.
   - NCCL needs two idle Slurm nodes, and the last GPU node starts in the k8s
     pool: on a 2-node pool, move it first (`make nccl` stops at once and says
     so otherwise). The smoke test checks that both ranks join over
     `NET/Socket` before the 36 jobs. g6.xlarge results are marked
     `final: false` (burstable network, see the result's notes).

     ```sh
     sudo /opt/al/bin/pool-move al-gpu-02 slurm
     sinfo -N -o '%N %T %E'             # both nodes idle
     make nccl-smoke                    # "check ok"
     make nccl                          # -> bench/results/net/nccl-*.json, exits non-zero if any run failed
     ```
     After the first run, keep one real log as a parser fixture (laptop, after
     `make results-pull`):

     ```sh
     cp "$(ls bench/results/net/nccl-all_reduce_perf-tcp-*.log | head -1)" bench/tests/fixtures/real/
     make test-py
     ```
   - `make train-sweep` → `bench/results/train/scale-*.json`, `scaling-summary-*.json`
     (`GPUS="1 2"` on a pool with fewer than four Slurm nodes)
   - export a checkpoint with `workloads/train/export_hf.py`, upload to
     `s3://<prefix>-models/al-gpt-124m-hf`, give the last node back to k8s,
     `make serve-bench` → `bench/results/serve/*.json`. If vLLM stays in
     `Init`, `make serve-diag` shows why its weight fetch fails.

     ```sh
     sudo /opt/al/bin/pool-move al-gpu-02 k8s
     kubectl -n serving rollout status deploy/vllm --timeout=15m
     make serve-bench
     ```
   - Kueue quota check: `kubectl create -f sched/k8s/kueue/quota-test.yaml` three times; extras stay `Pending`.
   - Topology (ADR 007, on-demand runs use a cluster placement group): `scontrol show topology` and
     `kubectl get nodes -L fleet.attestland/topology-block,fleet.attestland/topology-subblock`.
     Expect `unknown` on g6.2xlarge: EC2 reports no topology for it (ADR 010).
     `kubectl create -f sched/k8s/kueue/tas-example.yaml` is admitted in the single group.
   - `make ckpt-bench` (Phase 4, step 1) also runs here if there is time.
7. Update `fleet/burnin/baselines.yaml` with the measured medians (status: measured).
8. On the laptop: pull the results, then destroy. `make down` pulls first and
   destroys nothing if the pull fails (`FORCE=1` destroys anyway). The reaper
   terminates GPU nodes after 8 hours and `al-control` after 24, so results
   left on it survive a forgotten session for a day.

   ```sh
   make results-pull                # -> bench/results/, job logs in bench/results/logs/aws/
   make down POOL=gpu
   ```

## Phase 3 - Fleet automation

1. `make up POOL=gpu`; `make burnin` → per-node reports, `bench/results/burnin/`.
   One node at a time: the node and its peer are held out of their schedulers
   (`burnin-hold`) while it runs. Check: a node with a deliberately wrong driver
   fails only the software check.
2. healthd is deployed by `make up` in dry-run: it logs what it would drain.
   Before enforcing, check that the short diag passes on a GPU under load
   (`al-periodic-diag` skips busy nodes, but burn-in and the goodput runs do
   not), then enforce:

   ```sh
   # control node: one diag inside a running training job's allocation
   jid=$(sbatch --parsable -N 1 -w al-gpu-01 workloads/train/train.sbatch)
   until squeue -h -j "$jid" -o %T | grep -qx RUNNING; do sleep 10; done; sleep 120
   srun --jobid="$jid" --overlap dcgmi diag -r 1   # expect Pass on every test
   scancel "$jid"
   # laptop: enforce from now on
   echo 'export AL_HEALTHD_DRY_RUN=false' >> .env && . ./.env
   make configure POOL=gpu
   ```
3. Live fault: start training, then `python chaos/measure_drain.py --node al-gpu-02`
   → `bench/results/fleet/drain-xid79-*.json`.

## Phase 4 - Storage and reliability

1. `make ckpt-bench` → `bench/results/ckpt/ckpt-*.json` (124M model, and the
   10 GB and 50 GB states where the node's memory holds them twice: g6.2xlarge
   runs 10 GB, g6.xlarge the model only; `SIZES=` overrides).
2. `make ckpt-interval` → writes `checkpoint.interval_s` into `chaos/experiment.yaml`.
3. Deploy healthd with `--budget-min=8` for the experiment; `make goodput` (~10–12 h of
   GPU time: baseline + 2 arms × 3 repeats) → `bench/results/goodput/*.json`.
   That is longer than the reaper's 8 hours: tag the pool `keep=true` first (a
   tag change, no replacement), and back to `false` when it ends.

   ```sh
   # laptop (macOS sed)
   grep -q '^keep' infra/gpu-pool/terraform.tfvars || echo 'keep = false' >> infra/gpu-pool/terraform.tfvars
   sed -i '' 's/^keep .*/keep = true/' infra/gpu-pool/terraform.tfvars
   make infra-up POOL=gpu           # tags only; refuses any replacement
   # control node
   make goodput
   # laptop, afterwards
   sed -i '' 's/^keep .*/keep = false/' infra/gpu-pool/terraform.tfvars && make infra-up POOL=gpu
   ```
4. `make plots`; `make results-pull`; `make down POOL=gpu`.

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
