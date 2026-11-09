# ADR 007: Topology-aware placement and network health, merged from dc-orch

- Status: accepted
- Date: 2026-11-08
- Phase / section: Phases 2–3, spec 1.3, 3.1, 3.2

## Decision

The cloud-relevant parts of dc-orch (a separate laptop prototype of
network-aware GPU scheduling) move into this project:

1. **Physical position becomes scheduler topology.** The GPU pool is created in
   a GCE compact placement policy for on-demand runs. Each node's block and
   sub-block (metadata key `physical_host_topology`) are turned by
   `fleet/topology/topology.py` into node labels
   (`fleet.attestland/topology-block`, `fleet.attestland/topology-subblock`) and a
   Slurm `topology.conf`.
2. **Both schedulers use it.** Slurm runs `TopologyPlugin=topology/tree`.
   Kueue's built-in topology-aware scheduling uses a `Topology` with levels
   block → sub-block → hostname; the ResourceFlavor names it. Multi-pod jobs
   ask for placement with a pod-template annotation; single-pod work (vLLM, the
   quota test) declares `podset-unconstrained-topology`.
3. **Network faults go through healthd.** Two rules: a secondary ("rail") NIC
   down, and a NIC flapping, both stop new work on the node
   (`drain_after_jobs`).

## Context

dc-orch showed the idea on a kind cluster with fake GPUs: a custom
kube-scheduler score plugin kept a job inside one group of servers, and a
controller tainted nodes whose rail link was down (read with `docker exec`).
Its roadmap phases 3–5 and 7 (gang admission, telemetry, remediation, generated
topology) already exist here. The owner asked for the cloud-based parts only.

On GCE, a VM's position is hidden unless it is in a compact placement policy
(or on some A3/A4 types). G2 supports compact placement. Spot VMs with compact
placement are not documented, so Spot development runs skip it.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| Kueue topology-aware scheduling + Slurm topology/tree (chosen) | Kueue is already deployed and also does all-or-nothing admission; Slurm's plugin is standard; one data source feeds both | Kueue's exact Topology API version for v0.14.1 is unconfirmed (handled by applying at the CRD's storage version) |
| Port dc-orch's scheduler plugin to Kubernetes 1.34 | Custom scoring (best fit to limit fragmentation) | A second scheduler to maintain; no all-or-nothing admission; Kueue already covers placement |
| dc-orch's link controller | Simple | Duplicates healthd, bypasses the drain budget and incidents, and `docker exec` only works in a lab |
| Nothing | No work | Multi-node jobs land wherever the scheduler happens to put them |

## Consequences

- On today's 4 single-L4 VMs the effect is small: compact placement already
  keeps them close, and the rail rule cannot fire on a single-NIC VM. The
  plumbing matters for multi-GPU, multi-NIC machines and the 64/512-GPU designs.
- Nodes without position data are labelled `unknown` and form one group, so
  topology never blocks scheduling.
- Not merged (laptop-only): dc-orch's kind + containerlab placement lab and its
  default-vs-topology benchmark. dc-orch stays at `~/Code/dc-orch`, untouched.
- Revisit: measure step time with and without compact placement once the pool
  runs; if Kueue's placement is not good enough on larger machines, reconsider
  the custom plugin.
