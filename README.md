# Attestland

A small GPU cluster where every training and inference job can end with a
signed job receipt: hardware-backed proof of which machine, GPU driver,
container image, model and data produced a result. Previously named Attested
Compute; the original spec doc still carries that name ("Attested Compute -
Implementation Spec").

**New here? Read [docs/overview.md](docs/overview.md) first:** what the project
is, why it exists, the AI-infrastructure ideas behind it, every component and
why each was chosen, written for software engineers new to GPU infrastructure.

## Status

Everything runs on Google Cloud, in one project; AWS is the second choice for
the GPU pool. AWS sells no confidential GPU, so confidential receipts are
GCP-only (ADR 006).

All seven layers and five phases are implemented as code. Everything that
runs without a GPU or a cloud account has been run and tested on a laptop.
Everything else waits on accounts, quota and money; see
**docs/external-deps.md** for exactly what
exists and what does not.

| Phase | Built | Run locally | Waits on |
| --- | --- | --- | --- |
| 1 Local lab | containerlab fabric (SR Linux + FRR), lab tests, design note, provisioning sim | FRR fabric: 13/14 lab tests pass, 0.2 s convergence result file | Linux host for SR Linux, L3 VNIs, KVM/Tinkerbell |
| 2 GPU cluster | OpenTofu (GCP), Packer, Ansible, Slurm, k8s + Kueue + vLLM, pool-move, monitoring, NCCL/training/serving harnesses | training on CPU incl. exact resume (1 and 2 ranks), HF export | GCP project, L4 quota, budget |
| 3 Fleet automation | burn-in pipeline, healthd (Go) with GPU and network rules, topology-aware placement in Slurm and Kueue (ADR 007) | burn-in evaluator, healthd unit + integration tests, topology generator | GPU nodes |
| 4 Storage + reliability | Cloud Storage buckets (ADR 008), checkpoint bench, Young–Daly, chaos, goodput experiment | checkpoint tiers against an S3 API test server, goodput analysis on real logs | GPU pool |
| 5 Confidential receipts | receipt v1 schema, issuer, alverify (Google CPU tokens), GCP TDX bring-up, signed-image CI, overhead bench | issue + verify + all 6 negative tests (test platform) | GCP confidential A3 (H100) quota, GHCR, NRAS |

## Layout

```
infra/          OpenTofu: gpu-pool and cc-pool (GCP); Packer image; 8-hour reaper
ansible/        roles for drivers, Slurm, k8s, object-store credentials, monitoring, healthd, CC bring-up
versions.env    every pinned version
fleet/          provisioning (Tinkerbell + sushy-tools), burnin, healthd (Go), topology (GCE position -> labels, topology.conf)
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

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
make test                       # Go + Python tests
make lab-up LAB_NOS=frr && make lab-test LAB_NOS=frr && make lab-down LAB_NOS=frr
make receipts                   # builds bin/alissue, bin/alverify
```

On a Linux host, `make lab-up` (default `LAB_NOS=srl`) runs the spec's SR Linux fabric.

## Entry points

`make up|down POOL=gpu|cc`, `make provision N=3`, `make burnin`,
`make pool-move NODE=x TO=k8s|slurm`, `make nccl`, `make train-sweep`,
`make serve-bench`, `make ckpt-bench`, `make ckpt-interval`,
`make chaos FAULT=x TARGET=y`, `make goodput`, `make cc-overhead`, `make plots`.
The order of operations is in `docs/runbook.md`.
