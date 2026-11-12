# Attestland: the whole project in one document

This is the one document to read first. It explains what the project is, why it
exists, the ideas from AI infrastructure you need to follow it, every component,
and why each one was chosen over the alternatives. Terms are defined the first time
they appear and collected in the [glossary](#glossary) at the end.

Other documents go deeper on one thing each: `docs/runbook.md` (commands, in
order), `docs/external-deps.md` (what is still missing), `docs/adr/` (one file
per major decision), `network/design-note.md` (network sizing at 64 and 512 GPUs).

## Contents

1. [The short version](#1-the-short-version)
2. [Why the project exists](#2-why-the-project-exists)
3. [AI infrastructure in ten minutes](#3-ai-infrastructure-in-ten-minutes)
4. [Architecture at a glance](#4-architecture-at-a-glance)
5. [The components, layer by layer, and why each was chosen](#5-the-components-layer-by-layer-and-why-each-was-chosen)
6. [Decisions that cut across layers](#6-decisions-that-cut-across-layers)
7. [Where things stand](#7-where-things-stand)
8. [Glossary](#glossary)

---

## 1. The short version

Attestland is a small GPU cluster, built entirely as code, where every
training or inference job can end with a **signed job receipt**. A receipt is a
small JSON file that proves, with signatures from the hardware vendors, which
machine, which GPU driver, which container image, which model and which input
data produced a given output.

Around that idea the project builds the rest of a real GPU platform at small
scale: machines that test themselves before taking work, a scheduler for
training and another for serving, a fault-detection service that takes broken
machines out of rotation, checkpointing so training survives failures, a
network lab, monitoring, and failure-injection experiments. Every layer must
produce a measured number, recorded in a result file, rather than a claim.

It runs on AWS with 4 rented NVIDIA L4 GPUs for most work and on Azure with 1
NVIDIA H100 in a "confidential" VM for the receipts. Everything that does not need a
GPU also runs and is tested on a laptop.

## 2. Why the project exists

**The problem receipts solve.** When you send a model and data to someone
else's GPU (a cloud, a GPU marketplace, a partner) you get back an answer and a
bill. You cannot tell whether the answer came from the model you asked for,
whether the machine was the one you paid for, or whether anyone could see your
data on the way. Normal logs do not help, because whoever runs the machine
writes the logs. A receipt replaces "trust the operator" with "check the
signatures": the CPU and the GPU each sign a statement about their own state,
the container image is signed by the build pipeline, and the receipt ties those
statements to one specific job. Anyone can verify it later, offline, with one
command.

**The problem the rest of the cluster solves.** A receipt is only worth having
if the platform under it is real. Running GPUs is mostly about things software
engineers rarely see in web services: hardware that fails often and in strange
ways, jobs that must run on many machines at once and all fail if one does, a
network that decides how fast training goes, and machines that cost dollars per
hour, so idle or broken time is money. The project builds each of those parts
the way a production GPU platform would, at a scale one person can afford, and
measures each one.

**What it is not.** It is not production scale (2 to 5 GPUs, with a design note
for 64 and 512), not a high-availability service, not multi-tenant, and it does
no physical data-centre work (power, cooling, cabling). Those limits are
deliberate: the goal is a complete, honest, measurable version of every layer,
not a large one.

## 3. AI infrastructure in ten minutes

This section gives the ideas the rest of the document depends on, each mapped to
something you already know.

### GPUs and why they are special

A GPU is a processor with thousands of small cores and its own fast memory
(24 GB on an L4, 80 GB on the H100 used here). Machine-learning work is mostly
large matrix multiplications, which GPUs run tens of times faster than CPUs.
GPUs are also expensive (roughly $0.70 to $10 per GPU-hour to rent), fail more
often than CPUs, and depend on a matched set of software: a kernel driver, the
CUDA runtime, and libraries such as NCCL (below). A version mismatch anywhere in
that stack either breaks jobs or silently slows them, so this project pins every
version in one file.

### Training and inference

**Training** produces a model: the program reads a large dataset many times and
adjusts the model's parameters (its weights) to reduce error. It is a batch job
that runs for hours to months, and the output is a weights file.

**Inference** (or serving) uses a trained model to answer requests, like a web
service: it runs indefinitely, must answer quickly, and scales with traffic.

They want opposite things from infrastructure, which is why this project runs
two schedulers (section 5.3).

### Distributed training and why the network matters

A model too slow to train on one GPU is trained on many at once. The simplest
method, **data parallelism** (here PyTorch DDP), gives every GPU a full copy of
the model and a different slice of the data. After each step, every GPU has
computed its own changes to the weights, and all GPUs must average those
changes before the next step. That averaging is a collective operation called
**all-reduce**, done by NVIDIA's NCCL library over the network.

All-reduce runs on every step, and every GPU waits for it. So training speed is
often limited by the network, not the GPUs. Two consequences matter here:

- **Placement matters.** Two GPUs in the same server talk over a fast internal
  link; two servers under the same network switch are one hop apart; servers
  further apart cross more switches and compete with other traffic. Putting a
  job's machines close together makes every step faster.
- **One slow member slows everyone.** If one GPU or one network link is slow
  (a **straggler**), the whole job runs at its speed.

### Failures, checkpoints and goodput

Large GPU fleets fail constantly. Meta reported 419 interruptions in 54 days
while training Llama 3 on 16,384 GPUs, about one every three hours. A distributed
training job fails if any member fails. The defence is the **checkpoint**:
periodically save the full training state, so a failed job restarts from the
last save instead of from the beginning.

Checkpointing has a cost (the job pauses while saving), and so does not
checkpointing (work since the last save is lost on failure). The best interval
has a classic answer, the Young–Daly formula, which balances the two from the
save time and the mean time between failures.

**Goodput** is the share of paid GPU time that produced useful training
progress. Detection time, restart time, recomputed work, checkpoint pauses and
slow nodes all reduce it. It is the single number that tells you whether the
reliability machinery works, and this project measures it under injected
failures.

### Schedulers

A scheduler decides which job runs on which machine. Web services use one kind
(Kubernetes: keep N copies of this service running somewhere). Training uses
another kind (Slurm, from supercomputing: run this job on 4 whole machines at
once, all or nothing, close together, then give them back). The "all at once"
requirement is called **gang scheduling**: starting 3 of a job's 4 members is
worse than starting none, because the 3 sit idle waiting.

### Confidential computing and attestation

Normally whoever controls a machine (the cloud, its administrators, the
hypervisor) can read the memory of the programs running on it. **Confidential
computing** changes that: the CPU encrypts a VM's memory with keys the host
never sees (Intel TDX or AMD SEV-SNP), and recent NVIDIA GPUs (H100 and later)
do the same for GPU memory and for data moving between CPU and GPU.

**Attestation** is how you learn, from outside, that a machine really is in that
state. The hardware produces a report describing itself (firmware versions,
security settings, a hash of what booted), signed by a key burned into the chip.
A verification service run by the vendor (Microsoft Azure Attestation for the CPU
here, NVIDIA for the GPU) checks that report and returns a signed token. If you put your own random
value (a **nonce**) into the request, the token proves the attestation was made
for your request and is not a replay of an old one. Receipts are built on
exactly this.

## 4. Architecture at a glance

```
                       laptop (no cloud)                   AWS us-east-1                 Azure eastus2
  ┌──────────────────────────────────────────┐   ┌───────────────────────────┐   ┌────────────────────────────┐
  │ network lab (containerlab: SONiC/FRR)    │   │  GPU pool (phases 2-4)    │   │  confidential pool (5)     │
  │ unit + integration tests (Go, Python)    │   │  4 x g6.2xlarge (1 L4)    │   │  1 x NCC40ads_H100_v5      │
  │ receipt issue/verify on a test platform  │   │  + control VM + 4 S3      │   │  (SEV-SNP + H100, CC on)   │
  └──────────────────────────────────────────┘   │    buckets                │   │  + non-CC twin             │
                                                  └───────────────────────────┘   └────────────────────────────┘

  Layers inside the GPU pool (each is a directory in the repo):

  7 receipts        alissue in the confidential VM  ->  receipt.json  ->  alverify anywhere
  6 observability   Prometheus, Grafana, Loki, Alertmanager  |  chaos injection, goodput experiment
  5 storage         node-local NVMe (fast)  ->  S3 buckets (durable)  |  checkpoints
  4 workloads       GPT-2 training (PyTorch DDP)  |  vLLM model server
  3 scheduling      Slurm (training)  <-- pool-move -->  Kubernetes + Kueue (serving)
                    topology-aware placement in both (block / sub-block / host)
  2 network         one VPC, jumbo frames; lab fabric and 64/512-GPU design off-cloud
  1 fleet           Ansible install -> burn-in -> in service -> healthd drains on faults
  0 infrastructure  OpenTofu (VMs, network, budgets) + Ansible (configuration) + reaper
```

A training job's life, end to end:

1. `make up POOL=gpu` creates the VMs (OpenTofu) and configures them (Ansible).
2. Each GPU node runs **burn-in**, about 25 minutes of hardware tests, and only
   joins a scheduler if every test passes.
3. The researcher submits `sbatch -N 2 train.sbatch`. Slurm picks 2 nodes that
   are close together in the data centre (the topology step, when the cloud
   reports positions) and starts the container on both.
4. The training script checkpoints to local NVMe and copies each checkpoint to
   an S3 bucket.
5. If a GPU throws a fatal error, `healthd` sees it within seconds in the
   monitoring data, drains the node in Slurm, and opens an incident. Slurm
   requeues the job, which resumes from the last checkpoint on a healthy node.
6. Every timing above lands in a JSON result file under `bench/results/`, and
   `make plots` draws the charts from those files.

An inference job with a receipt: the confidential VM runs vLLM from a signed
image, answers a batch of requests, then `alissue` hashes the model, requests
and responses, collects CPU and GPU attestations bound to those hashes, and
signs the receipt. `alverify` checks it anywhere, with no network access.

## 5. The components, layer by layer, and why each was chosen

Each subsection lists what a component does in software-engineering terms and
why it was picked. Where a decision record exists, it is cited as ADR NNN.

### 5.0 Infrastructure and configuration

| Component | What it is | Why this choice |
| --- | --- | --- |
| **AWS** (GPU pool) and **Azure** (confidential pool) | Where the VMs run | AWS rents single-L4 VMs (`g6.2xlarge`) and had GPU quota when GCP's was blocked. AWS sells no confidential GPU, so the confidential H100 VM runs on Azure, which offers it on-demand with documented attestation. Two clouds mean two accounts, budgets and reaper passes (ADR 010). |
| **OpenTofu** (`infra/`) | Declares VMs, networks, firewalls and budgets as code, like Terraform | Same language and providers as Terraform under an open-source licence. Creating and destroying the whole cluster per working session is what keeps costs down, so it must be one command. |
| **Ansible** (`ansible/`, 29 roles) | Configures the VMs over SSH after they exist | Needs no agent on the machines and handles a handful of VMs well. Each role does one thing (driver, Slurm, Kubernetes, monitoring) and skips work already done, so re-running it on a live node is safe. |
| **`versions.env`** | Every pinned version in one file | GPU stacks break on version mismatch. One file feeds Make, Ansible and the benchmarks, and every result file records it, so any number can be traced to exact versions. |
| **8-hour reaper** (`infra/reaper/`) | A scheduled GitHub Action that deletes old project VMs | A forgotten GPU VM costs real money overnight. Anything older than 8 hours without `keep=true` is deleted. |
| **Ubuntu 22.04** | Operating system on every VM | Azure's confidential GPU VMs use Canonical's 22.04 confidential image, the confidential VM and its twin must match, and NVIDIA's GPU Operator supports 22.04. Standard security support for 22.04 ends in May 2027, so a move to 24.04 is planned (ADR 009). |

### 5.1 Fleet: getting machines into service and out of it

**Node lifecycle.** Every machine has a state: provisioning, burn-in, in
service, draining, drained, in repair. The state is stored in both schedulers
(a Kubernetes label and a Slurm state with a reason), so either one can be asked
"is this node usable?".

| Component | What it is | Why this choice |
| --- | --- | --- |
| **Burn-in** (`fleet/burnin/`) | 8 checks a node must pass before taking work: NVIDIA's DCGM diagnostics, memory error counters, a sustained-load test that watches for throttling, GPU-to-GPU bandwidth inside the node and to a peer node, disk speed, network speed and packet size, and software versions | New and repaired GPUs fail early more often than later ("infant mortality"). Catching a bad GPU before it joins a 4-node job saves the whole job. A node with the wrong driver fails only the software check, so the report says exactly what is wrong. |
| **healthd** (`fleet/healthd/`, Go) | A service that watches monitoring data and drains bad nodes in Slurm and Kubernetes | Rules live in a YAML file (`rules.yaml`), each a monitoring query plus an action, like alerting rules that act. It respects a **drain budget** (never take out more than 1 node or 25% per hour), so a monitoring bug cannot empty the cluster. It opens an incident for every drain. |
| controller-runtime | The Go library Kubernetes controllers are built on | Gives healthd a standard reconcile loop and fake clients for tests; 21 tests run without a cluster. |
| **Provisioning simulation** (`fleet/provisioning/`) | Blank VMs on a Linux host, booted over the network and installed automatically through a fake server-management API (Redfish, via sushy-tools) and Tinkerbell | Real data centres install bare-metal servers this way. Cloud VMs skip it, so a simulation is the only way to exercise it without hardware. |

**Network health rules.** healthd also watches the network cards:
a data network card that goes down, or a link that keeps flapping, stops new
work on that node. The rules target secondary network cards, the ones larger
GPU machines use to connect each GPU to the network ("rails", section 5.2). On
the current single-card L4 VMs they never fire, because losing the only card
makes the node unreachable, which Kubernetes and Slurm already treat as down.

### 5.2 Network

| Component | What it is | Why this choice |
| --- | --- | --- |
| **VPC with jumbo frames** | The cloud network for the GPU pool, with 9,001-byte packets (the AWS default inside a VPC) | Bigger packets mean fewer per-packet costs for all-reduce over TCP, which is all the L4 VMs have. |
| **Network lab** (`network/containerlab/`) | A data-centre network running in containers on a laptop: 2 spine switches, 4 leaf switches, test hosts | Real data-centre networks are configured as code and tested before changes reach production. containerlab runs real switch software in containers, so routing configs can be tested like code. |
| **SONiC, FRR as fallback** | The switch operating systems in the lab | SONiC is the open-source switch OS that most AI Ethernet fabrics run on white-box and NVIDIA switches; its container build (`docker-sonic-vs`) runs the same configuration layer and FRR routing stack. It is x86-only, so on a Mac the same fabric is rendered for plain FRR, an open-source router (ADR 012, ADR 004). |
| **eBGP underlay, EVPN/VXLAN overlay** | Routing between switches, plus virtual networks on top | The standard design for modern data-centre fabrics: every switch link is routed (no single big broadcast domain), traffic spreads over all equal paths, and tenants get isolated virtual networks. The lab tests check routing, isolation, path spreading and how fast traffic recovers when a link fails (0.2 s measured on FRR; SONiC not yet run). |
| **Rail-optimised lab topology** | A second lab fabric where GPU *n* of every server connects to switch *n* | Large GPU clusters wire servers this way so that the same-numbered GPUs across servers, which talk most during all-reduce, are one switch hop apart. |
| **Design note** (`network/design-note.md`) | Switch counts, link counts, bottlenecks and failure domains for 64 and 512 GPUs | The rented cluster is too small to show these problems, so the note reasons about them with stated assumptions. |
| **NCCL benchmark** (`bench/nccl/`) | Measures all-reduce bandwidth between GPUs, over TCP and RDMA | This is the number that sets the ceiling on multi-node training speed. RDMA (network cards writing directly into another machine's memory) is not available on g6.2xlarge; on AWS it means EFA, which starts at g6.8xlarge (ADR 010). |

### 5.3 Scheduling

**Two schedulers, one fleet (ADR 001).** Training runs under **Slurm** and
inference under **Kubernetes**. Each GPU node belongs to one of them at a time,
and `make pool-move NODE=x TO=k8s` moves it: drain it in one scheduler, wait for
its work to finish, hand it to the other. With only 4 GPUs there is no room for
two separate clusters, and each workload gets the scheduler built for it.
Alternatives considered: Slurm running inside Kubernetes (Slinky, judged too
young), Kubernetes only with Kueue and JobSet (weak at requeue-from-checkpoint
and the launch model training scripts expect), or Slurm only (no service
discovery, health probes or rolling updates for serving).

| Component | What it is | Why this choice |
| --- | --- | --- |
| **Slurm 25.05**, built from source | The batch scheduler for training | Gang allocation, whole-node jobs, requeue on failure with a warning signal before pre-emption, accounting, and placement by network topology. Built from source because distribution packages lag and lack the REST API (with JWT auth) that healthd uses. |
| **Pyxis + Enroot** | Lets Slurm jobs run containers, without root | Training uses NVIDIA's PyTorch container; Enroot runs it unprivileged and Pyxis adds `--container-image` to `srun`. Docker's root daemon is not wanted on shared batch nodes. |
| **Kubernetes 1.34 via kubeadm** | The service scheduler for inference | A managed service (EKS, AKS) owns its nodes. Here the same VM must be able to leave Kubernetes and join Slurm, so the cluster is self-managed. |
| **NVIDIA GPU Operator**, partly disabled | Makes GPUs visible to Kubernetes (device plugin, GPU labels) | The driver, container toolkit and GPU exporter stay on the host, because Slurm uses them too; two copies would conflict (ADR 005). |
| **Kueue 0.14** | A job queue on top of Kubernetes: quotas, priorities, all-or-nothing admission | Plain Kubernetes starts pods as soon as any fit. Kueue holds a job until all its pods fit, enforces the GPU quota of the Kubernetes pool, and lets serving pre-empt batch evaluation. |
| **pool-move** (`sched/pool-move.sh`) | Moves a node between schedulers | One command, refuses to move a node healthd has drained, and updates the Kueue GPU quota to match the new pool size. |

**Topology-aware placement.** Section 3 explained why a
multi-node job should run on machines that are close together. VMs in an EC2
**cluster placement group** are placed close together, and for some instance
types EC2 also reports where each sits: the network nodes above it, read as a
*block* and a *sub-block*. It does not report this for g6.2xlarge, so on today's
pool every node is `unknown`; the plumbing is ready for a type that has it. The
project uses that data in three steps:

1. The GPU pool is created in a cluster placement group for timed (on-demand)
   runs.
2. Ansible reads each node's position and `fleet/topology/topology.py` turns it
   into node labels (`fleet.attestland/topology-block`,
   `fleet.attestland/topology-subblock`) and a Slurm `topology.conf`. A node with
   no position data is labelled `unknown`, which puts all such nodes in one
   group: correct, because no data means no distance to optimise.
3. Both schedulers then use it. Slurm's `topology/tree` plugin prefers the
   fewest switch hops for a multi-node job. Kueue's topology-aware scheduling
   places a Kubernetes job's pods in the smallest block or sub-block that fits;
   a job asks for this with a pod annotation, and single-pod services such as
   vLLM say they have no constraint.

Kueue's built-in topology placement is used instead of a custom scheduler
plugin: it also handles all-or-nothing admission and is already deployed. The
plumbing matters on multi-GPU machines and at the 64- and 512-GPU sizes in the
design note (ADR 007).

### 5.4 Workloads

| Component | What it is | Why this choice |
| --- | --- | --- |
| **GPT-2 small (124M parameters), nanoGPT-style** (`workloads/train/`) | The training workload | Small enough to train on L4s in about 1.5 hours, large enough to be a real language model, and widely reproduced, so results can be compared with published numbers. The code resumes exactly: a run restarted from a checkpoint produces the same losses as one that never stopped (tested on 1 and 2 processes). |
| **FineWeb-Edu** (2B-token sample) | The training text | Open, well documented, licensed for this use (ODC-By). |
| **PyTorch DDP** | Data-parallel training across GPUs | The standard, simplest way to train a model that fits on one GPU on many GPUs. |
| **MFU** (model FLOPs utilisation) | Training speed as a share of the GPU's theoretical peak | Tokens per second depends on model size; MFU is comparable across models and GPUs. Healthy small-scale training lands around 20 to 40%. |
| **vLLM 0.11** (`sched/k8s/vllm/`) | The model server for inference | The most widely used open-source serving engine. It batches requests continuously and manages GPU memory in pages, which gives several times the throughput of naive serving. |
| **Serving sweep** (`workloads/serve/`) | Raises the request rate until latency targets break | Reports throughput at a stated latency target (99th-percentile time to first token ≤ 500 ms, time between tokens ≤ 50 ms), the honest way to compare serving setups. |
| **Hugging Face export** | Converts the trained model to the standard format vLLM loads | Tested: the exported model gives the same outputs as the reference library. |

### 5.5 Storage and checkpoints

| Component | What it is | Why this choice |
| --- | --- | --- |
| **Node-local NVMe** (`/scratch`) | Fast disk inside each GPU VM | Saving to local disk takes seconds, so the training pause is short. It disappears with the VM, so it is only the first copy. |
| **S3 buckets** (`infra/gpu-pool/storage.tf`) | AWS's object store | The durable copy of checkpoints, datasets, models and receipts. There is no storage server to run or patch, and nodes use their instance role, so there are no keys to leak. It replaced MinIO, whose community edition is archived (ADR 008, ADR 010). |
| **PyTorch Distributed Checkpoint (DCP)** | Saves and loads distributed training state | The native PyTorch format, and it can save in the background. Each node keeps a full copy, so a surviving node restores from its own disk and a replacement node downloads from S3. |
| **Young–Daly interval** (`storage/interval.py`) | Computes how often to checkpoint | Uses the measured save time and a failure rate to pick the interval that wastes the least time; the result feeds the goodput experiment. |

### 5.6 Observability and reliability

| Component | What it is | Why this choice |
| --- | --- | --- |
| **DCGM + DCGM exporter** | NVIDIA's GPU telemetry: errors, temperatures, clocks, memory faults | The only source for GPU hardware errors (XID codes, memory errors). healthd's rules read it. |
| **Prometheus, Alertmanager** | Metrics database and alert routing | The standard open-source pair; healthd queries Prometheus with the same query language as the alerts. |
| **Loki + Alloy** | Log storage and the agent that ships logs | Same query style as Prometheus, cheap to run, and one Grafana for both. |
| **Grafana**, 5 dashboards generated from code | Fleet, GPU health, training, serving, goodput | Dashboards are generated by a script, so they are reviewed and versioned like code. |
| **Chaos injection** (`chaos/`) | Deliberately breaks things: GPU fault, node loss, process crash, network delay, packet loss, slow GPU, full disk | Each fault has a revert step. Reliability claims are only believable if the failure was injected and the recovery timed. |
| **Goodput experiment** | The same training run under the same seeded failure schedule, with manual recovery (an operator resubmits after 15 minutes) against automatic recovery | Turns "automatic recovery helps" into a measured percentage, with lost time broken down by cause. |

### 5.7 Confidential receipts

| Component | What it is | Why this choice |
| --- | --- | --- |
| **Azure `Standard_NCC40ads_H100_v5` confidential VM** | 1 H100 NVL GPU, AMD SEV-SNP for CPU memory, NVIDIA confidential mode for GPU memory | Offered on-demand with documented attestation; AWS has no confidential GPU (ADR 010). A twin `Standard_NC40ads_H100_v5` with the same GPU and confidential mode off measures what confidential computing costs. |
| **Microsoft Azure Attestation** via `AttestationClient` | Turns the CPU's SEV-SNP report (read through the vTPM) into a token signed by Microsoft, carrying our nonce | A signed token can be checked offline with the attestation service's published keys, and it carries the launch measurement of the VM image, which the policy pins. |
| **NVIDIA NRAS** via `nvattest` | Turns the GPU's report into a token signed by NVIDIA, with driver and firmware versions | NVIDIA-signed means the GPU claim does not depend on the machine's operator. A local verifier exists but its results are unsigned, so the policy rejects them by default. |
| **Nonce binding** | SHA-256 of the model, input and output hashes, the image digest and a public key generated inside the VM for this job | Both attestations carry this one value, so they vouch for this job's content and this key, and that key signs the receipt. An attestation copied from another receipt fails the check. |
| **Canonical JSON (RFC 8785) + ECDSA P-256** | How the receipt is serialised and signed | JSON has many valid spellings of the same data; the canonical form makes the signed bytes unambiguous. P-256 is supported by every crypto library and hardware key store. |
| **Sigstore keyless signing** | The CI pipeline signs the container image with a short-lived certificate tied to its GitHub identity, logged publicly | No long-lived signing key to protect or rotate. The verifier checks that the image came from this repository's workflow on `main`. |
| **`alissue` and `alverify`** (Go) | Issue a receipt inside the VM; verify one anywhere | Go builds one static binary with no runtime to install. `alverify` runs 7 checks (schema, signature, nonce, CPU, GPU, image, content). Six tampering cases must each fail, and do: changed output byte, swapped image, attestation copied from an older receipt, wrong signing key, driver outside policy, body edited after signing. |
| **Verification policy** (`receipts/policy/`) | A YAML file of trusted keys and allowed values | Separates "what is signed" from "what this verifier accepts", so a verifier can pin driver versions or a VM image measurement without code changes. It pins Microsoft's token keys and NVIDIA's. |

## 6. Decisions that cut across layers

- **Every claim is a number in a result file.** Benchmarks write JSON with the
  configuration, versions and raw measurements; charts are drawn from those
  files by `make plots`, never by hand. A number that is not in `bench/results/`
  does not appear in any document.
- **Short-lived infrastructure.** Each working session runs `make up`, does its
  work and ends with `make down`; budgets alert at 50, 80 and 100%, and the
  reaper deletes stragglers.
- **Pinned versions everywhere**, recorded in every result file.
- **One decision record per real choice** (`docs/adr/`), with the options and
  what would make us change our mind.
- **Assumptions are written down.** Where a vendor detail could not be checked
  without hardware (tool flags, token claim names), the code says so and
  `docs/external-deps.md` lists how to confirm it.

## 7. Where things stand

All seven layers and five phases are built as code and everything that can run
on a laptop has run: 55 Python and 41 Go tests (counting subtests) pass. Nothing
but a one-VM test launch has run in the cloud yet.
AWS GPU quota is in place and the Azure H100 quota was granted on 2026-11-10;
the twin VM still needs Azure SKU access. Of the eight headline numbers, one exists so far (network
convergence in the lab, 0.2 s on the FRR fabric; the SONiC fabric has not run yet). `docs/external-deps.md` lists every external
dependency with its status and every assumption with how to confirm it, and
`docs/runbook.md` gives the commands in order.

## Glossary

| Term | Meaning |
| --- | --- |
| all-reduce | Every GPU contributes a value and every GPU receives the combined result; the core operation of data-parallel training |
| attestation | A hardware-signed report of a machine's security state, checked by a vendor service |
| block / sub-block | Groups of physically close machines, from the cloud's topology data; VMs in the same sub-block have the shortest network path |
| burn-in | Hardware tests a node must pass before taking work |
| checkpoint | A saved copy of a training run's full state, used to resume after a failure |
| confidential computing (CC) | Hardware memory encryption that hides a VM's data from the host and its operators |
| CUDA | NVIDIA's programming platform and runtime for GPUs |
| DCGM | NVIDIA's GPU health and telemetry service |
| DDP | PyTorch's DistributedDataParallel: one model copy per GPU, gradients averaged each step |
| drain | Stop sending new work to a node (and optionally stop current work) so it can be repaired |
| EVPN / VXLAN | Standard protocols for virtual networks on top of a routed data-centre network |
| gang scheduling | Start all members of a multi-node job together, or none |
| goodput | Share of paid GPU time that produced useful training progress |
| MFU | Model FLOPs utilisation: achieved training compute as a share of the GPU's peak |
| NCCL | NVIDIA's library for collective operations (all-reduce and others) between GPUs |
| nonce | A one-time value put into a request so the response cannot be a replay |
| rail | In a rail-optimised network, the switch that GPU *n* of every server connects to |
| RDMA | Network transfers directly between machines' memory, bypassing the CPU and kernel |
| receipt | This project's signed JSON proof of what produced a job's output |
| Spot VM | Discounted cloud capacity the provider can take back at short notice |
| straggler | One member of a distributed job that is slower than the rest and slows all of them |
| TDX / SEV-SNP | Intel's and AMD's CPU technologies for confidential VMs |
| XID | An NVIDIA driver error code; some (48, 79) mean the GPU must be taken out of service |
