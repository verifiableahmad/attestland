# Attestland - the only entry points.
#
#   make up POOL=gpu|cc          create a pool (OpenTofu) and configure it (Ansible);
#   make down POOL=gpu|cc        destroy it; ends every working session
#   make reaper-up               reaper's AWS role, Azure app and repo variables (once)
#   make lab-host-up / -down     AWS bare-metal Linux/KVM host for lab-up and provision
#   make lab-up / lab-test       containerlab leaf-spine fabric (laptop)
#   make provision N=3           blank VMs -> Burn-in via Redfish + Tinkerbell (Linux/KVM host)
#   make pool-move NODE=x TO=k8s|slurm
#   make chaos FAULT=<name> TARGET=<node>
#   make test                    every test that runs without a GPU or a cloud

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
include versions.env
export

POOL ?= gpu
PY ?= $(CURDIR)/.venv/bin/python
TOFU ?= tofu
ANSIBLE ?= ansible-playbook
INVENTORY ?= ansible/inventory/$(POOL).ini
BIN := $(CURDIR)/bin
export ANSIBLE_CONFIG := $(CURDIR)/ansible/ansible.cfg

.PHONY: help up down infra-up infra-down configure reaper-up reaper-down \
        lab-host-up lab-host-sync lab-host-ssh lab-host-down \
        lab-up lab-test lab-down lab-rail-up provision provision-down \
        pool-move chaos burnin nccl train train-sweep serve-bench ckpt-bench ckpt-interval \
        goodput receipts alverify cc-overhead healthd test test-go test-py lint plots

help:
	@grep -E '^#   make' Makefile | sed 's/^#   //'

# --- Infrastructure ---------------------------------------------------------
up: infra-up configure

infra-up:
	$(TOFU) -chdir=infra/$(POOL)-pool init -input=false
	$(TOFU) -chdir=infra/$(POOL)-pool apply -input=false -auto-approve
	$(TOFU) -chdir=infra/$(POOL)-pool output -raw ansible_inventory > $(INVENTORY)

configure:
	$(PY) scripts/versions2yaml.py >/dev/null
	ansible-galaxy collection install -r ansible/requirements.yml >/dev/null
	$(ANSIBLE) -i $(INVENTORY) ansible/site-$(POOL).yml

down:
	$(TOFU) -chdir=infra/$(POOL)-pool destroy -input=false -auto-approve
	rm -f $(INVENTORY)

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
# LAB_NOS=srl (default, spec) or frr (fallback; runs on Docker Desktop, L2 VNIs only there)
LAB_NOS ?= srl
LAB_TOPO := $(if $(filter frr,$(LAB_NOS)),fabric-frr.clab.yml,fabric.clab.yml)

lab-up:
	$(PY) network/containerlab/gen.py --out network/containerlab/configs
	./network/containerlab/clab.sh deploy -t $(LAB_TOPO) --reconfigure --max-workers 2

lab-test:
	LAB_NOS=$(LAB_NOS) $(PY) -m pytest -q -rs network/containerlab/tests

lab-down:
	./network/containerlab/clab.sh destroy -t $(LAB_TOPO) --cleanup

lab-rail-up:
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
nccl:
	./bench/nccl/run-all.sh

train:
	sbatch workloads/train/train.sbatch

train-sweep:
	./workloads/train/sweep.sh

serve-bench:
	$(PY) workloads/serve/sweep.py --config workloads/serve/sweep.yaml

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
