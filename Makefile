# Attestland - the only entry points.
#
#   make durable-up              S3 buckets and the OpenTofu state bucket (AWS, once; outlives every pool)
#   make up POOL=gpu|cc          create a pool (OpenTofu) and configure it (Ansible);
#                                POOL=gpu CLOUD=nebius: the GPU pool on Nebius (ADR 013)
#   make sync                    copy this working tree to the control node (after a local edit)
#   make results-pull            result files and job logs from the control node to bench/results
#   make down POOL=gpu|cc        pull the results, then destroy the pool; ends every working session
#   make control-ssh             shell in /opt/al/repo on the control node; run data, nccl, train*, serve-bench there
#   make reaper-up               reaper's AWS role, Azure app and repo variables (once)
#   make lab-host-up / -down     AWS bare-metal Linux/KVM host for lab-up and provision
#   make lab-up / lab-test       containerlab leaf-spine fabric: SONiC (Linux x86), LAB_NOS=frr (laptop)
#   make provision N=3           blank VMs -> Burn-in via Redfish + Tinkerbell (Linux/KVM host)
#   make pool-move NODE=x TO=k8s|slurm
#   make chaos FAULT=<name> TARGET=<node>
#   make test                    every test that runs without a GPU or a cloud

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
include versions.env
export

POOL ?= gpu
# The GPU pool's cloud: aws (infra/gpu-pool) or nebius (infra/gpu-pool-nebius).
CLOUD ?= aws
POOL_DIR := infra/$(POOL)-pool$(if $(filter nebius,$(CLOUD)),-nebius)
PY ?= $(CURDIR)/.venv/bin/python
TOFU ?= tofu
ANSIBLE ?= ansible-playbook
INVENTORY ?= ansible/inventory/$(POOL)$(if $(filter nebius,$(CLOUD)),-nebius).ini
BIN := $(CURDIR)/bin
export ANSIBLE_CONFIG := $(CURDIR)/ansible/ansible.cfg
# On the laptop: the IAM user al-operator (runbook, prerequisites), never the
# account root user. Not on the cluster (role common writes /etc/fleet/node.env),
# where `make chaos` and `make goodput` use the node's instance role.
ifeq ($(wildcard /etc/fleet/node.env),)
AWS_PROFILE ?= attestland
endif

.PHONY: help up down infra-up infra-down configure sync results-pull control-ssh durable-up state-migrate \
        reaper-up reaper-down \
        lab-host-up lab-host-sync lab-host-ssh lab-host-down \
        lab-image lab-up lab-test lab-down lab-rail-up provision provision-down \
        pool-move chaos burnin data nccl nccl-smoke train train-sweep serve-bench serve-diag ckpt-bench ckpt-interval \
        goodput receipts alverify cc-overhead healthd test test-go test-py lint plots

help:
	@grep -E '^#   make' Makefile | sed 's/^#   //'

# --- Infrastructure ---------------------------------------------------------
up: infra-up configure

# Durable tier (infra/durable, D-2): the buckets and the OpenTofu state bucket,
# in their own state, so `make down` never touches them.
# Interactive on purpose: review the plan (imports, no destroys) and type yes.
durable-up:
	$(TOFU) -chdir=infra/durable init -input=false
	$(TOFU) -chdir=infra/durable apply

# The AWS GPU pool keeps its state in the durable tier's bucket (OPS-2).
AWS_GPU := $(and $(filter gpu,$(POOL)),$(filter aws,$(CLOUD)))
BACKEND_CONFIG = $(if $(AWS_GPU),-backend-config=bucket=$$($(TOFU) -chdir=infra/durable output -raw state_bucket) \
                 -backend-config=region=$$($(TOFU) -chdir=infra/durable output -raw region))

# Once, to move a local terraform.tfstate of infra/gpu-pool into the state bucket.
state-migrate:
	$(TOFU) -chdir=infra/gpu-pool init -input=false -migrate-state -force-copy $(BACKEND_CONFIG)

# The plan is applied only if it replaces no VM (scripts/plan-guard.py); a
# replaced control node takes the results on it. ALLOW_REPLACE=1 to go ahead.
infra-up:
ifneq ($(AWS_GPU),)
	@$(TOFU) -chdir=infra/durable output -raw state_bucket >/dev/null 2>&1 || { \
	  echo "no durable tier yet: run make durable-up first (runbook, Phase 2 step 3)" >&2; exit 1; }
	@[ ! -s infra/gpu-pool/terraform.tfstate ] || grep -qs '"type": *"s3"' infra/gpu-pool/.terraform/terraform.tfstate || { \
	  echo "infra/gpu-pool still has a local state: run make state-migrate first (runbook, Phase 2 step 3)" >&2; exit 1; }
endif
	$(TOFU) -chdir=$(POOL_DIR) init -input=false $(BACKEND_CONFIG)
	$(if $(AWS_GPU),./scripts/aws-quota.sh $(POOL_DIR))
	$(TOFU) -chdir=$(POOL_DIR) plan -input=false -out=tfplan
	$(TOFU) -chdir=$(POOL_DIR) show -json tfplan | $(PY) scripts/plan-guard.py
	$(TOFU) -chdir=$(POOL_DIR) apply -input=false tfplan
	$(TOFU) -chdir=$(POOL_DIR) output -raw ansible_inventory > $(INVENTORY)

configure:
	$(PY) scripts/versions2yaml.py >/dev/null
	ansible-galaxy collection install -r ansible/requirements.yml >/dev/null
	$(ANSIBLE) -i $(INVENTORY) ansible/site-$(POOL).yml

# Code edits only: the working tree and the venv, no restarts and no pool
# changes (`make configure` re-runs every role).
sync:
	$(ANSIBLE) -i $(INVENTORY) ansible/sync.yml

# Result files and job logs live on the control node until pulled. `make down`
# pulls first and destroys nothing if that fails; FORCE=1 destroys anyway.
CONTROL_HOST = $$(sed -n 's/^al-control ansible_host=\([^ ]*\).*/\1/p' $(INVENTORY))
CONTROL_SSH = ssh -i $$(sed -n 's/^ansible_ssh_private_key_file=//p' $(INVENTORY)) -o StrictHostKeyChecking=accept-new

results-pull:
	rsync -az -e "$(CONTROL_SSH)" al@$(CONTROL_HOST):/opt/al/results/ bench/results/
	rsync -az -e "$(CONTROL_SSH)" al@$(CONTROL_HOST):/scratch/logs/ bench/results/logs/$(CLOUD)/
	@echo "results in bench/results (job logs: bench/results/logs/$(CLOUD), not committed)"

down:
ifeq ($(POOL),gpu)
	@$(MAKE) --no-print-directory results-pull || [ "$(FORCE)" = 1 ] || { \
	  echo "results-pull failed, nothing destroyed; FORCE=1 destroys anyway and loses what is on al-control" >&2; exit 1; }
endif
	$(TOFU) -chdir=$(POOL_DIR) init -input=false $(BACKEND_CONFIG) >/dev/null
	$(TOFU) -chdir=$(POOL_DIR) destroy -input=false -auto-approve
	rm -f $(INVENTORY)

# The control node is the login node: `make configure` copies this working tree
# to /opt/al/repo there and shares it, /opt/al/results, /scratch/logs and
# /scratch/runs with the GPU nodes.
control-ssh:
	$(CONTROL_SSH) -t al@$(CONTROL_HOST) 'cd /opt/al/repo && exec bash -l'

# Reaper identities (infra/reaper): the github provider reads GITHUB_TOKEN.
reaper-up:
	$(TOFU) -chdir=infra/reaper init -input=false
	GITHUB_TOKEN=$${GITHUB_TOKEN:-$$(gh auth token)} $(TOFU) -chdir=infra/reaper apply -input=false

reaper-down:
	GITHUB_TOKEN=$${GITHUB_TOKEN:-$$(gh auth token)} $(TOFU) -chdir=infra/reaper destroy -input=false

# Lab host (infra/lab-host, ADR 011): c5.metal; the reaper deletes it after 8 hours.
LAB_HOST = $$($(TOFU) -chdir=infra/lab-host output -raw public_ip)
LAB_SSH = ssh -o StrictHostKeyChecking=accept-new al@$(LAB_HOST)

lab-host-up:
	$(TOFU) -chdir=infra/lab-host init -input=false
	$(TOFU) -chdir=infra/lab-host apply -input=false -auto-approve
	@echo "waiting for cloud-init (bare metal boots take 5-15 min)"
	until $(LAB_SSH) -o ConnectTimeout=5 test -e /var/lib/al-lab-ready 2>/dev/null; do sleep 20; done
	$(MAKE) lab-host-sync

# Copy this working tree (committed or not) and a venv for the lab tests.
lab-host-sync:
	rsync -az --delete --exclude .venv --exclude .terraform --exclude '*.tfstate*' \
	  --exclude '*.tfvars' --exclude 'clab-*' -e "ssh -o StrictHostKeyChecking=accept-new" \
	  ./ al@$(LAB_HOST):attestland/
	$(LAB_SSH) "cd attestland && python3 -m venv .venv && .venv/bin/pip install -q \
	  $$(grep -E '^(pytest|PyYAML|jsonschema)==' requirements-dev.txt | tr '\n' ' ')"

lab-host-ssh:
	$(LAB_SSH)

lab-host-down:
	$(TOFU) -chdir=infra/lab-host destroy -input=false -auto-approve

# --- Layer 2: network lab ---------------------------------------------------
# LAB_NOS=sonic (default, ADR 012; docker-sonic-vs, Linux x86 only) or frr
# (fallback; runs on Docker Desktop, L2 VNIs only there)
LAB_NOS ?= sonic
LAB_TOPO := $(if $(filter frr,$(LAB_NOS)),fabric-frr.clab.yml,fabric.clab.yml)

# docker-sonic-vs is a build artifact, not on a registry: download and load it once.
lab-image:
	./network/containerlab/sonic-image.sh

lab-up: $(if $(filter frr,$(LAB_NOS)),,lab-image)
	$(PY) network/containerlab/gen.py --out network/containerlab/configs
	./network/containerlab/clab.sh deploy -t $(LAB_TOPO) --reconfigure --max-workers 2

lab-test:
	LAB_NOS=$(LAB_NOS) $(PY) -m pytest -q -rs network/containerlab/tests

lab-down:
	./network/containerlab/clab.sh destroy -t $(LAB_TOPO) --cleanup

lab-rail-up: lab-image
	$(PY) network/containerlab/gen.py --out network/containerlab/configs
	./network/containerlab/clab.sh deploy -t rail.clab.yml --reconfigure

# --- Layer 1: fleet ---------------------------------------------------------
N ?= 3
provision:
	./fleet/provisioning/provision.sh $(N)

provision-down:
	./fleet/provisioning/teardown.sh

burnin:
	$(ANSIBLE) -i $(INVENTORY) ansible/burnin.yml

healthd:
	cd fleet/healthd && CGO_ENABLED=0 go build -o $(BIN)/healthd ./cmd/healthd

# --- Layer 3: scheduling ----------------------------------------------------
pool-move:
	./sched/pool-move.sh $(NODE) $(TO)

# --- Layer 4: workloads -----------------------------------------------------
# Corpus into the datasets bucket and every idle Slurm node's /scratch (NODES=n to choose).
data:
	sbatch --wait -N $${NODES:-$$(sinfo -h -N -o %T | grep -cx idle)} workloads/train/prepare.sbatch

nccl:
	./bench/nccl/run-all.sh

# Before the 36-job sweep: 2 nodes, 64 MB, both ranks joined over NET/Socket (NCCL-6).
nccl-smoke:
	SMOKE=1 ./bench/nccl/run-all.sh

train:
	sbatch workloads/train/train.sbatch

train-sweep:
	./workloads/train/sweep.sh

serve-bench:
	$(PY) workloads/serve/sweep.py --config workloads/serve/sweep.yaml

# Why vLLM's weight fetch fails (I-16): checks, plus one short-lived pod.
serve-diag:
	./sched/k8s/vllm/diag.sh

# --- Layer 5: storage -------------------------------------------------------
ckpt-bench:
	sbatch storage/ckpt_bench.sbatch

ckpt-interval:
	$(PY) storage/interval.py --write chaos/experiment.yaml

# --- Layer 6: reliability ---------------------------------------------------
chaos:
	./chaos/inject.sh $(FAULT) $(TARGET)

goodput:
	$(PY) chaos/goodput_experiment.py --config chaos/experiment.yaml

# --- Layer 7: receipts ------------------------------------------------------
receipts:
	cd receipts && CGO_ENABLED=0 go build -o $(BIN)/alissue ./issuer/cmd/alissue
	cd receipts && CGO_ENABLED=0 go build -o $(BIN)/alverify ./verify/cmd/alverify
	cd receipts && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o $(BIN)/alissue-linux-amd64 ./issuer/cmd/alissue

cc-overhead:
	$(PY) receipts/overhead/overhead.py --config receipts/overhead/overhead.yaml

alverify: receipts

# --- Tests and plots --------------------------------------------------------
test: test-go test-py

test-go:
	cd fleet/healthd && go test ./...
	cd receipts && go test ./...

test-py:
	$(PY) -m pytest -q bench workloads storage chaos network/containerlab/tests/test_gen.py fleet/burnin fleet/provisioning fleet/topology sched/tests receipts/overhead

plots:
	$(PY) bench/plots.py
