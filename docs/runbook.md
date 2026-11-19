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
   The workflow runs on its schedule from `main` and when started by hand; a
   push does not start it. Phase 2, step 8 says how often it really runs.

## Nebius GPU pool (alternative to AWS, ADR 013)

The GPU pool can run on Nebius instead of AWS, or at the same time: the pools
share no resources, no state key and no inventory, so each is applied and
destroyed on its own. Every Phase 2-4 step is the same with `CLOUD=nebius` added
to every make target that touches the pool: `up`, `configure`, `sync`,
`results-pull`, `down`, `control-ssh`, `burnin` (the target picks the inventory
from `CLOUD`; without it, it uses the AWS pool). Skip Phase 2 step 3's
`state-migrate` and its AWS buckets: step 4 below makes the Nebius ones. AWS is
used for one thing only, the OpenTofu state bucket of `make durable-up` (made
once, no AWS pool needed); the nodes and the data never touch AWS.

Not ported: the reaper, `make chaos` (node-loss) and `make goodput`, which only
know AWS and Azure. `make ckpt-bench` runs, but on a network SSD, so it is not
comparable with AWS.

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
   2 GPU nodes that is 3 VMs, 3 disks and 4 vCPUs. The docs list a default L40S
   quota of 2 GPUs, enough for the default pool; check the number in the
   console. Without the vCPU increase the control VM cannot start.
3. Check that the image family exists for both platforms:

   ```sh
   nebius compute image list-public --region eu-north1 | grep -B2 -A8 ubuntu24.04-driverless
   ```
4. State bucket and durable tier, once. The state bucket is the AWS one
   (`make durable-up`, Phase 2 step 3, skip it if it exists; needs
   `AWS_PROFILE=attestland`). `durable-nebius-up` makes the four Nebius buckets
   and the service-account S3 key in their own state: `make down` never touches
   them, and OpenTofu refuses to delete them (`prevent_destroy`).

   ```sh
   cp infra/gpu-pool-nebius/terraform.tfvars.example infra/gpu-pool-nebius/terraform.tfvars
   cp infra/durable-nebius/terraform.tfvars.example infra/durable-nebius/terraform.tfvars
   # both: project_id, and tenant_id in the durable one (step 1)
   make durable-up                  # only if infra/durable does not exist yet
   make durable-nebius-up           # review the plan, type yes
   ```
5. Configure and create. Fill `admin_cidrs` in the pool's tfvars with your
   current public IP. `.env` holds the cluster secrets (Phase 2, step 2 creates
   it). The first `make up` surfaces the unverified items of ADR 013 (cpu-d3
   preset, security rules, pinned NVIDIA packages on the 24.04 repository).

   ```sh
   curl -s https://checkip.amazonaws.com      # -> admin_cidrs = ["<ip>/32"]
   . ./.env
   make up POOL=gpu CLOUD=nebius
   make control-ssh CLOUD=nebius
   ```
   If only the Ansible half failed: `. ./.env && make configure POOL=gpu CLOUD=nebius`.
6. No reaper covers Nebius yet: destroy the pool at the end of every session.
   This removes the VMs, disks and network rules only; the buckets and their
   contents stay, and cost storage only. The corpus and models are already
   there, so `make data` only copies them to `/scratch` next time.

   ```sh
   make results-pull CLOUD=nebius   # job logs: bench/results/logs/nebius/
   make down POOL=gpu CLOUD=nebius
   nebius compute instance list --format jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.state}{"\n"}{end}'
   ```
   To remove the buckets as well (after emptying them), delete the
   `prevent_destroy` lines in `infra/durable-nebius/storage.tf` and run
   `tofu -chdir=infra/durable-nebius destroy`.

## Phase 2 - GPU cluster (AWS, ~60 GPU-hours budgeted)

1. `cp infra/gpu-pool/terraform.tfvars.example infra/gpu-pool/terraform.tfvars`, fill it.
   The plan assumes 4 x g6.2xlarge. Since 2026-11-15 the account's on-demand G
   quota is 8 vCPUs, which fits two g6.xlarge, so until AWS raises it use:

   ```hcl
   gpu_nodes         = 2
   gpu_instance_type = "g6.xlarge"
   ```
2. Export secrets: `AL_MUNGE_KEY_B64`, `AL_SLURM_JWT_KEY_B64`,
   `AL_GRAFANA_PASSWORD`, `AL_SLURMDBD_DB_PASSWORD`, optional
   `AL_ALERT_WEBHOOK_URL` (Alertmanager's webhook format; unset means log-only).
   They are generated locally, not fetched. Write them once to the git-ignored
   `.env` and source it (`. ./.env`) in every shell that runs `make up` or
   `make configure`. New keys on a re-apply restart munge and drop running
   jobs. `make configure` stops at its first task if the munge or JWT key is
   missing.

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
4. Create the pool with `. ./.env && make up POOL=gpu`. It uses on-demand
   nodes; set `spot = true` only once the account has G Spot quota. `make up`
   checks the G/VT vCPU quota first, and refuses a plan that replaces a VM,
   because a replaced `al-control` takes the results on it; `ALLOW_REPLACE=1`
   after `make results-pull` lets it through. If EC2 returns
   `InsufficientInstanceCapacity`, move only the GPU nodes: set `control_az` to
   the current zone, then `az` to another one, and re-apply. If only the Ansible
   half failed, `. ./.env && make configure POOL=gpu` re-runs it without
   OpenTofu.

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
5. Data, on the control node. The last GPU node starts in the k8s pool, and on
   a 2-node pool the NCCL and 2-GPU training runs need both nodes in Slurm, so
   move it first; one `make data` then covers both nodes.

   `make data` builds the corpus in the training container on one Slurm node,
   uploads it to `s3://<prefix>-datasets/fineweb-edu-2b` and copies it to the
   `/scratch` of every idle Slurm node. The corpus is already in the bucket, so
   on later pools it skips the build and only copies. `/scratch` belongs to
   each node, so run it on every new pool, and again after a `pool-move` or a
   node replacement; it then copies only what is missing.

   ```sh
   sudo /opt/al/bin/pool-move al-gpu-02 slurm
   sinfo -N -o '%N %T %E'             # both nodes idle
   make data                          # NODES=n to choose the node count
   ```
6. The timed runs, on the control node, with `spot = false` (the default).
   They run in the order below: NCCL, then training, then serving, which
   needs a checkpoint from training. Result files land in `/opt/al/results` on
   the control node (`AL_RESULTS_DIR`), each NCCL one with its raw log beside
   it; `make results-pull` copies them to `bench/results/` on the laptop.

   `make nccl` and `make train-sweep` submit one Slurm job at a time and wait
   for it. Ctrl-C stops the wait, not the job: the job keeps its node until it
   ends, so cancel it with `scancel <job id>` (`squeue` shows the id). The
   targets also stop when the SSH session closes, so start the long ones with
   `nohup` and follow their output file instead.

   - NCCL. `make nccl` runs NVIDIA's nccl-tests, built from source by the
     `cuda_nccl` role at the `NCCL_TESTS_REF` tag in `versions.env`. It times
     all_reduce, all_gather, reduce_scatter and sendrecv, 3 repeats each, over
     every transport the nodes have. With one GPU per node only the TCP jobs
     measure anything: intra needs 2 GPUs in a node and RDMA needs EFA, and both
     skip. On 2 x g6.xlarge that is 12 result files in about 3 hours.

     The numbers describe the instance's network, not the code, so one run per
     instance type is enough: later sessions on the same type can skip it, and
     step 7 uses the files already in `bench/results/net/`. g6.xlarge results
     are marked `final: false`, because its network runs on burst credits and
     slows down during the run (the result's notes say so). Bus bandwidth is in
     GB/s, as nccl-tests prints it, although the JSON fields are named
     `*_gbps`. Each run so far is written up in `docs/results/`.

     The smoke test is one short all_reduce that checks both ranks join over
     `NET/Socket`; run it before the full run.

     ```sh
     make nccl-smoke                    # "check ok"
     nohup make nccl > /scratch/logs/nccl-$(date -u +%Y%m%dT%H%M%SZ).out 2>&1 &
     # -> /opt/al/results/net/nccl-*.json; exits non-zero if any run failed
     ```

     The parser's test fixture in `bench/tests/fixtures/real/` holds a real log
     from 2026-11-17. Replace it only if the nccl-tests output changes, on the
     laptop after `make results-pull`:

     ```sh
     cp "$(printf '%s\n' bench/results/net/nccl-all_reduce_perf-tcp-*.log | head -1)" bench/tests/fixtures/real/
     make test-py
     ```

   - Training. `make train-sweep` trains the 124M model on 1, 2 and 4 GPUs,
     3 repeats each, on 60M tokens (the full budget is for the goodput runs).
     On a pool with fewer than four Slurm nodes set `GPUS="1 2"`; without it
     the first 4-GPU job is rejected and the sweep stops there. It also stops
     at the first run that fails. At the end, `scaling.py` writes the summary.
     Start it from a `make control-ssh` shell: `scaling.py` finds the results
     through `AL_RESULTS_DIR`, which only login shells set, and fails with
     "need a 1-GPU baseline" without it. If that happens, run
     `.venv/bin/python workloads/train/scaling.py` on the laptop after
     `make results-pull`.

     A 1-GPU run took 33 minutes on a g6.xlarge on 2026-11-18: about 5 minutes
     to import the container image on the node, then about 36,500 tokens/s at
     22% MFU. The 2-GPU runs go over the same throttled network as NCCL and
     have not been timed yet. Plan for 2.5 to 3 hours for the 6 runs.

     Each run ends with a checkpoint (model and optimizer, about 1.6 GB) in
     `/scratch/ckpt/<run>/step_<N>/` on its node, with a copy in
     `s3://<prefix>-checkpoints/<run>/step_<N>/`. The copy outlives the pool, so
     a later session can serve without training again.

     ```sh
     GPUS="1 2" nohup make train-sweep > /scratch/logs/train-sweep-$(date -u +%Y%m%dT%H%M%SZ).out 2>&1 &
     squeue                                          # one gpt124m job at a time
     tail -f /scratch/runs/scale-g1-r1/progress.jsonl
     # -> /opt/al/results/train/scale-*.json, scaling-summary-*.json
     ```

   - Serving. vLLM serves the model in `s3://<prefix>-models/al-gpt-124m-hf`,
     and the bucket is empty until you upload one. Until then its pod restarts
     in a loop (`CrashLoopBackOff`). The weight fetch succeeds on an empty
     prefix, and vLLM then stops with "Invalid repository ID or local directory
     specified: '/models/current'". That is expected on a pool without a
     model.

     To give it one, convert a training checkpoint to Hugging Face format with
     `workloads/train/export_hf.py`, upload it, move the second node back to
     k8s and run the benchmark. A 60M-token sweep checkpoint is enough, because
     the benchmark measures serving speed, not answer quality. Start from the
     S3 copy: the control node copies it into `/scratch/runs`, which every node
     shares, so the export can run on any Slurm node, also on a later pool.

     The export below has not been run on a pool yet. `export_hf.py` imports
     `transformers`, which `requirements-container.txt` does not list, so the
     first run may need it added there.

     ```sh
     # control node; list the checkpoints with: aws s3 ls s3://<prefix>-checkpoints/ --recursive | grep complete
     ck=scale-g1-r1/step_00001831
     aws s3 sync "s3://<prefix>-checkpoints/$ck" "/scratch/runs/ckpt/$ck"
     source /etc/fleet/versions.env
     srun -N 1 --container-image="${PYTORCH_IMAGE/\//#}" \
       --container-mounts=/scratch:/scratch,/opt/al/repo:/opt/al/repo --container-workdir=/opt/al/repo \
       bash -c "pip install -q --no-deps -r workloads/train/requirements-container.txt &&
                python workloads/train/export_hf.py --config workloads/train/configs/gpt124m.yaml \
                  --ckpt /scratch/runs/ckpt/$ck --out /scratch/runs/export/al-gpt-124m-hf"
     aws s3 sync /scratch/runs/export/al-gpt-124m-hf s3://<prefix>-models/al-gpt-124m-hf
     sudo /opt/al/bin/pool-move al-gpu-02 k8s
     kubectl -n serving get deploy vllm              # 0/0: kubectl -n serving scale deploy/vllm --replicas=1
     kubectl -n serving rollout status deploy/vllm --timeout=15m
     make serve-bench                                # -> /opt/al/results/serve/*.json
     ```

     If vLLM stays in `Init`, the weight fetch is failing, and `make serve-diag`
     shows why.

   - Checks, if there is time:
     - Kueue quota: `kubectl create -f sched/k8s/kueue/quota-test.yaml` three
       times; the extra ones stay `Pending`.
     - Topology (ADR 007; on-demand runs use a cluster placement group):
       `scontrol show topology` and
       `kubectl get nodes -L fleet.attestland/topology-block,fleet.attestland/topology-subblock`.
       Expect `unknown`: EC2 reports no topology for g6.2xlarge (ADR 010), and
       g6.xlarge has not been checked. `kubectl create -f sched/k8s/kueue/tas-example.yaml`
       is admitted in the single group.
     - `make ckpt-bench` (Phase 4, step 1).
7. Update `fleet/burnin/baselines.yaml` with the measured medians (status: measured).
8. On the laptop: pull the results, then destroy. `make down` pulls first and
   destroys nothing if the pull fails (`FORCE=1` destroys anyway).

   ```sh
   make results-pull                # -> bench/results/, job logs in bench/results/logs/aws/
   make down POOL=gpu
   ```

   What stays: the S3 buckets (corpus, checkpoints and models) and whatever
   `results-pull` copied to `bench/results/`. What goes: each node's `/scratch`,
   and anything on `al-control` that was not pulled. So the next pool needs
   `make data` again (a copy, not a rebuild), but not a new NCCL run on the same
   instance type, and not a new training run if a checkpoint is already in the
   bucket.

   Destroy the pool by hand; don't count on the reaper. It terminates GPU nodes
   older than 8 hours and `al-control` after 24, but only when its scheduled
   GitHub run fires. The schedule says hourly, but in November 2026 GitHub ran
   it only every 4 to 9 hours. A forgotten pool can live well past 8 hours, and
   a pool that is past 8 hours can lose its GPU nodes in the middle of a run.
   For a run that goes past 8 hours, tag the pool `keep=true` (Phase 4, step 3)
   or pause the workflow until `make down`:

   ```sh
   gh workflow disable reaper.yml
   gh workflow enable reaper.yml    # after make down
   ```

## Phase 3 - Fleet automation

1. `. ./.env && make up POOL=gpu`; `make burnin` → per-node reports, `bench/results/burnin/`.
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
3. Deploy healthd with `--budget-min=8` for the experiment; `make goodput` (10 to 12 h of
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
4. `. ./.env && make up POOL=cc` (bring-up runs `receipts/bringup/cc-bringup.sh`). Check
   `/var/lib/fleet/bringup-maa-claims.json`: `x-ms-attestation-type` must be
   `sevsnpvm`, and the nonce must appear under `x-ms-runtime`.
5. Fill `receipts/policy/prod/`: MAA JWKS (`<maa_url>/certs`), NRAS JWKS, Sigstore
   trusted root, the launch measurement and the VBIOS from bring-up.
6. On the VM: `receipts/bringup/run-job.sh job-001 receipts/bringup/sample-requests.jsonl /models/current out/`.
7. Laptop: `bin/alverify out/receipt.json --policy receipts/policy/prod/policy.yaml --outputs out/outputs.jsonl`.
8. Negative tests on the real receipt: edit a byte, swap the digest, etc.; each must fail.
9. Overhead benchmark, once the twin's SKU access is granted: `create_twin = true`,
   `. ./.env && make up POOL=cc`, `make cc-overhead` → `bench/results/cc/overhead-*.json`.
10. `make down POOL=cc`.
