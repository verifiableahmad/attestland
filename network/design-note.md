# Backend fabric design note: 64 and 512 GPUs

A 64-GPU cluster needs 4 switches and 256 optics; a 512-GPU cluster needs 24
switches and 2,048 optics, both non-blocking (1:1) on 64-port 400G switches.
The numbers come from `network/sizing.py`; rerun it to change any input.

## Assumptions

- 8 GPUs per node (H100/B200 HGX class), one 400G backend NIC per GPU.
- Separate frontend and storage networks; the backend carries only GPU-to-GPU
  traffic (NCCL over RoCEv2).
- Named switch: **64 × 400G, 25.6 Tb/s, radix R = 64** (Tomahawk 4 class).
  The 51.2 Tb/s generation (64 × 800G split to 128 × 400G, R = 128) is shown
  as the comparison row.
- Every link optical, two transceivers per link. In-rack DAC or AEC cables
  would replace some GPU-to-leaf optics; that lowers cost, not the count of links.
- Non-blocking: each leaf has R/2 ports down and R/2 up.

## Sizing

Formulas: leaves = GPUs ÷ (R/2); uplinks = leaves × R/2; spines = uplinks ÷ R,
with at least 2 spines so a spine failure is never total.

| GPUs | Radix | Nodes | Leaves | Spines (formula/used) | Links per leaf–spine pair | GPU–leaf links | Leaf–spine links | Optics | Switches | Leaves per rail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 64 | 64 | 8 | 2 | 1 / 2 | 16 | 64 | 64 | 256 | 4 | ¼ (rail group of 4 rails per leaf) |
| 64 | 128 | 8 | 1 | 1 / 2 | 32 | 64 | 64 | 256 | 3 | ⅛ (one switch holds all 8 rails) |
| 512 | 64 | 64 | 16 | 8 / 8 | 4 | 512 | 512 | 2,048 | 24 | 2 |
| 512 | 128 | 64 | 8 | 4 / 4 | 16 | 512 | 512 | 2,048 | 12 | 1 |

Reading the table:

- **64 GPUs, R = 64.** Two leaves. Rails 1–4 of every node land on leaf 1,
  rails 5–8 on leaf 2 (a "rail group" per leaf). Each leaf sends 16 links to
  each of 2 spines; a single spine would do (formula minimum 1), but then one
  spine failure halves nothing and kills everything cross-leaf.
- **64 GPUs, R = 128.** One 51.2T switch takes all 64 GPU ports. A single tier
  is non-blocking and needs no spines; the price is one failure domain for the
  whole backend. Two switches with rails split 4/4 and no interconnect works
  only if cross-rail traffic always goes over NVLink first (NCCL PXN).
- **512 GPUs, R = 64.** 16 leaves, 2 per rail (nodes 1–32 and 33–64 of rail
  n). 8 spines; each leaf has 4 links to every spine. GPU n of every node shares
  a rail, so the ring/tree steps NCCL runs between same-index GPUs stay one hop
  (leaf only) for half the nodes, and three hops (leaf–spine–leaf) otherwise.
- **512 GPUs, R = 128.** Exactly one leaf per rail: every same-rail exchange is
  one hop. 4 spines carry only cross-rail traffic. This is why 51.2T switches
  are the default for new 512-GPU builds: half the switches, fewer hops.
- **Ceiling.** A non-blocking two-tier fabric tops out at R²/2 GPUs: 2,048 at
  R = 64 and 8,192 at R = 128. Above that, a third tier (or a larger radix) is needed.

## Links per GPU

| Network | Per node | Per GPU | Oversubscription | Purpose |
| --- | --- | --- | --- | --- |
| Backend (compute) | 8 × 400G | 400 Gb/s | 1:1 | NCCL collectives, RoCEv2 |
| Storage | 2 × 200G | 50 Gb/s | 2:1 at the storage spine | Checkpoints, dataset reads |
| Frontend | 1 × 100G | 12.5 Gb/s | 4:1 | SSH, images, control plane, inference ingress |
| Out-of-band | 1 × 1G | - | - | BMC (Redfish), console |

Checkpoint sizing check: a 70B-parameter model with Adam (≈1.1 TB of state
across the job) written by 64 nodes at 2 × 200G = 50 GB/s per node aggregate
completes in about 0.35 s of network time; storage back-end throughput, not
the links, is the limit. Keeping storage off the frontend matters because a
checkpoint burst would otherwise saturate the 100G frontend for minutes.

## Bottlenecks, ranked

1. **ECMP hash collisions with few, large flows.** NCCL opens a small number
   of long-lived QPs per GPU pair. Static 5-tuple hashing can put two elephant
   flows on one spine link while another idles; a 2-flow collision halves that
   pair's bandwidth and the whole collective runs at the slowest link's pace.
   Mitigations: rail-optimised placement (fewer flows cross spines), more QPs
   per connection (`NCCL_IB_QPS_PER_CONNECTION`), adaptive routing or packet
   spraying on the switches, and flowlet switching.
2. **Optics failure and flapping.** At 2,048 optics and a typical 0.1–0.5%
   annual failure rate, expect several optic failures a year, each killing a
   running job. Link flaps (degraded but not dead) are worse: they cause
   retransmits and timeouts that look like software bugs. Monitor FEC
   corrected/uncorrected counters and pre-emptively drain.
3. **Incast.** All-to-all (MoE) and reduce-scatter fan many senders into one
   receiver port; the egress queue fills faster than ECN can slow the senders.
4. **PFC pause storms and head-of-line blocking.** A slow receiver pauses its
   upstream port, which pauses its own upstream, spreading congestion to
   innocent flows sharing those queues; in the worst case a cyclic buffer
   dependency deadlocks. PFC watchdog and ECN tuned to fire well before XOFF.
5. **Oversubscription.** Avoided on the backend by design (1:1). It still
   appears in practice when a spine or links are down: one of 8 spines lost
   = 12.5% less cross-leaf capacity.
6. **Storage traffic on the frontend.** Avoided by the separate storage
   network; listed because it is the most common shortcut and the most common
   cause of checkpoint stalls at scale.

## Congestion control: RoCEv2 vs InfiniBand

| | RoCEv2 + PFC + ECN (DCQCN) | InfiniBand |
| --- | --- | --- |
| Loss handling | Lossless class via PFC; ECN marks to slow senders | Credit-based link-level flow control; lossless by construction |
| Congestion control | DCQCN, tuned per fabric (Kmin/Kmax/Pmax, rate-increase timers) | Built-in congestion control, managed by the subnet manager |
| Routing | ECMP; adaptive routing on newer Ethernet ASICs | Adaptive routing standard; SHARP in-network reduction |
| Operations | Standard Ethernet tooling and staff; more knobs to tune | Separate fabric manager, smaller vendor pool |
| Cost | Lower per port, multi-vendor | Higher, single vendor |

Adaptive routing helps most where bottleneck 1 bites: few large flows on a
multi-path fabric. It needs receiver-side reordering tolerance (NICs that
handle out-of-order RDMA), which current ConnectX-7 / BlueField-3 class NICs
provide. Recommendation for both sizes: RoCEv2 with ECN-first tuning, PFC as a
backstop with a watchdog, and adaptive routing enabled where the ASIC supports
it. InfiniBand is the safer choice when the team has no RoCE tuning experience
and the budget allows it.

## Failure domains

| Failure | 64 GPUs (R = 64) | 512 GPUs (R = 64) | Effect on a running job | Scheduler response |
| --- | --- | --- | --- | --- |
| GPU NIC or its optic | 1 GPU | 1 GPU | That rank stalls; collective times out; job fails | Drain node (healthd), requeue on spare |
| Leaf | 4 rails × 8 nodes = 32 GPUs | 1 rail × 32 nodes = 32 GPUs | Every job touching those nodes loses one rail and fails | Treat a leaf as a block: drain all its nodes, requeue elsewhere |
| Spine | 50% of cross-leaf capacity | 12.5% of cross-leaf capacity | Job keeps running after ECMP reconverges (sub-second with BFD), slower | Alert only; no drain |
| Spine–leaf optic | 1/32 of a leaf's uplinks | 1/32 of a leaf's uplinks | Brief loss on rehash, then ~3% slower | Alert; replace in maintenance window |

Placement rules that follow:

- Place a job on the fewest leaf groups that fit it (Slurm `topology/block`
  or `topology/tree`, Kueue topology-aware scheduling), so a leaf failure hits
  as few jobs as possible and most traffic stays one hop.
- Keep one or two spare nodes per leaf group so a requeued job restarts in
  the same group without waiting.
- Checkpoint interval follows the fleet MTBF (see `storage/interval.py`): 512
  GPUs fail roughly 8× as often as 64, so the optimal interval shrinks by √8 ≈ 2.8×.
