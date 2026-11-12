# Attestland

A small GPU cluster where every training and inference job can end with a
signed job receipt: hardware-backed proof of which machine, GPU driver,
container image, model and data produced a result. Previously named Attested
Compute; the original spec doc still carries that name ("Attested Compute -
Implementation Spec").

**For additional details, Read [docs/overview.md](docs/overview.md):** what the project
is, why it exists, the AI-infrastructure ideas behind it, every component and
why each was chosen, written for software engineers new to GPU infrastructure.

## Layout

```
infra/          OpenTofu: gpu-pool (AWS), cc-pool (Azure), lab-host (AWS bare metal);
                reaper (8-hour sweep script + its AWS role, Azure app and repo variables)
ansible/        roles for drivers, Slurm, k8s, object-store credentials, monitoring, healthd, CC bring-up
versions.env    every pinned version
scripts/        versions.env -> Ansible vars
images/         vllm-receipts container image (built and signed by CI)
.github/        workflows: ci, images, reaper
fleet/          provisioning (Tinkerbell + sushy-tools), burnin, healthd (Go), topology (EC2 position -> labels, topology.conf)
network/        containerlab fabric + tests, RoCE QoS, 64/512-GPU design note
sched/          Slurm configs, k8s manifests (GPU Operator, Kueue + topology-aware scheduling, vLLM), pool-move
workloads/      train (nanoGPT-style GPT-2, DDP, DCP checkpoints), serve (vLLM sweep)
storage/        checkpoint benchmark, Young–Daly interval
observability/  Prometheus rules, Alertmanager, Loki, Grafana dashboards (generated)
chaos/          fault injection, failure schedule, goodput experiment + analysis
receipts/       schema, issuer (alissue), verifier (alverify), CC bring-up, overhead bench
bench/          result-file library, NCCL parser, plots; results/ holds every number
docs/           ADRs, runbook, external dependencies
```

## Quick start (laptop, no cloud)

Needs Python 3.12+, Go 1.24 and a running Docker (Docker Desktop on macOS).

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
make test                       # Go + Python tests
make lab-up LAB_NOS=frr && make lab-test LAB_NOS=frr; make lab-down LAB_NOS=frr
make receipts                   # builds bin/alissue, bin/alverify (+ linux-amd64 issuer)
```

On a Linux host, `make lab-up` (default `LAB_NOS=srl`) runs the spec's SR Linux
fabric. Without one, `make lab-host-up` creates an AWS bare-metal host for it
(ADR 011, ~$4.08/h).

## Entry points

`make up|down POOL=gpu|cc`, `make lab-host-up|lab-host-down`, `make provision N=3`, `make burnin`,
`make pool-move NODE=x TO=k8s|slurm`, `make nccl`, `make train-sweep`,
`make serve-bench`, `make ckpt-bench`, `make ckpt-interval`,
`make chaos FAULT=x TARGET=y`, `make goodput`, `make cc-overhead`, `make plots`.
The order of operations is in `docs/runbook.md`.
