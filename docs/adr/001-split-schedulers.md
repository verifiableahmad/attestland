# ADR 001: Slurm for training, Kubernetes for inference, one fleet

- Status: accepted
- Date: 2026-11-07
- Phase / section: Phase 2, spec 3.4

## Decision

Training runs under Slurm and inference under Kubernetes. Every GPU node
belongs to exactly one of them at a time, and `make pool-move` moves a node
between the two in one command. healthd drains a bad node in both, whichever
pool it is in.

## Context

The fleet is 2–4 rented GPUs, so it cannot afford a dedicated cluster per
workload type. The two workloads want opposite things from a scheduler:

| Need | Training | Inference |
| --- | --- | --- |
| Unit of work | A job that runs to completion | A service that runs indefinitely |
| Placement | All N nodes at once (gang), whole nodes, close together | One replica anywhere a GPU is free |
| Launch | MPI/torchrun-style: one rank per GPU, rendezvous from the scheduler | One container per replica, discovered through a Service |
| Failure handling | Requeue the whole job, resume from checkpoint | Restart the pod, keep serving from other replicas |
| Updates | None mid-run | Rolling updates, readiness probes |
| Scaling | Fixed size per job | Autoscaling on load |

Slurm does the left column natively: `sbatch -N 2 --exclusive`, gang allocation,
topology-aware placement (`topology/tree`, `topology/block`), `--requeue` with
a pre-emption signal, and accounting. Kubernetes does the right column
natively: Deployments, Services, probes, rolling updates, HPA, and an
ecosystem (vLLM, KServe) built for it.

## Options considered

| Option | For | Against | When we would choose it |
| --- | --- | --- | --- |
| **Slurm + Kubernetes, nodes move between them (chosen)** | Each workload gets the scheduler built for it; well-trodden HPC and serving stacks | Two control planes; a node is idle while it moves; pool membership must be kept consistent | Small fleets with both workloads and an ops team that knows both |
| Slurm on Kubernetes (Slinky: slurm-operator, slurm-bridge) | One control plane for nodes; Slurm semantics for training pods | Young project; slurmd in pods adds a layer between Slurm and the GPU; harder to reason about cgroups and device isolation | Kubernetes-first organisations that need Slurm's job semantics for researchers |
| Kubernetes only: Kueue + JobSet or LeaderWorkerSet for multi-node training | One scheduler; Kueue gives quotas, gang admission and topology-aware scheduling | No srun/MPI launch model; requeue-from-checkpoint and pre-emption signals are hand-built; less mature accounting | Fleets that are mostly inference with occasional training, or teams with no HPC background |
| Slurm only, inference as long-running jobs | One scheduler | No service discovery, health probes or rolling updates; vLLM restarts lose traffic | Batch-only inference (offline evaluation) |

## Consequences

- Easy: each workload uses its scheduler's native tools; failures in one
  control plane do not take down the other.
- Hard: two sources of truth for node state. Mitigated by keeping lifecycle
  state as a k8s label *and* Slurm state+reason (spec 1), and by healthd
  draining in both.
- A node moving pools is idle while its work drains. With 4 nodes this is the
  main cost; at 64+ nodes, static pools sized by demand would replace moves.
- The Kueue `nvidia.com/gpu` quota must track the k8s pool size; pool-move
  patches it on every move.
- Revisit when Slinky reaches a stable release with device isolation parity,
  or when Kueue's topology-aware scheduling and JobSet cover requeue with
  checkpoint signalling.
