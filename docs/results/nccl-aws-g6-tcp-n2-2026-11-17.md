# NCCL over TCP, 2 nodes, AWS g6.xlarge: 2026-11-17

One `make nccl` run: 4 collectives x 3 repeats, 2 ranks (1 L4 GPU per node) over the NCCL socket transport. The sweeps ran from 11:54 to 14:56 UTC.

## Setup

- Instances: 2 x g6.xlarge (NVIDIA L4), us-east-1; nodes al-gpu-02, al-gpu-01
- Software: NCCL 2.27.7-1+cuda12.9, CUDA 12.9.1, driver 580.196.07, kernel 6.8.0-1069-aws
- Transport check: `NET/Socket` on both ranks
- Code: git `a4b32e013f70510b15f1be936bdf749ce4a48792-dirty`
- Sweep: 30 message sizes per file, x2 steps, 5 warmup + 20 timed iterations. The requested range is 8 B to 8 GB; nccl-tests lowered the top to 6.34 GiB when the buffers did not fit.
- Marked not final by the parser: burstable network, 2.5 Gbps baseline / 10 Gbps burst (ADR 010 assumes g6.2xlarge)

## Results (bus bandwidth, GB/s)

Units are gigabytes per second, as nccl-tests prints them (the JSON fields are named `*_gbps` but hold GB/s; x8 for Gbit/s). `avg` is the mean over all sizes, `peak` the best size. `probe pre/post` is a 64 MiB all_reduce before and after the sweep.

| Collective | Rep | Start (UTC) | Avg | Peak | Probe pre | Probe post |
|---|---:|---|---:|---:|---:|---:|
| all_gather_perf | 1 | 12:04 | 0.368 | 1.01 | 1.00 | 0.51 |
| all_gather_perf | 2 | 13:13 | 0.217 | 0.74 | 1.04 | 0.39 |
| all_gather_perf | 3 | 14:22 | 0.217 | 0.73 | 1.07 | 1.05 |
| all_reduce_perf | 1 | 11:54 | 0.482 | 1.06 | 0.98 | 1.06 |
| all_reduce_perf | 2 | 13:02 | 0.209 | 0.70 | 1.05 | 0.53 |
| all_reduce_perf | 3 | 14:11 | 0.205 | 0.60 | 1.10 | 0.52 |
| reduce_scatter_perf | 1 | 12:16 | 0.219 | 0.78 | 0.98 | 0.53 |
| reduce_scatter_perf | 2 | 13:25 | 0.206 | 0.79 | 1.03 | 0.53 |
| reduce_scatter_perf | 3 | 14:33 | 0.214 | 0.74 | 1.08 | 0.47 |
| sendrecv_perf | 1 | 12:39 | 0.235 | 0.76 | 1.00 | 0.61 |
| sendrecv_perf | 2 | 13:48 | 0.227 | 0.66 | 0.99 | 0.53 |
| sendrecv_perf | 3 | 14:56 | 0.234 | 0.68 | 1.04 | 0.51 |

Mean over the 3 repeats:

| Collective | Avg | Peak |
|---|---:|---:|
| all_gather_perf | 0.267 | 0.83 |
| all_reduce_perf | 0.299 | 0.79 |
| reduce_scatter_perf | 0.213 | 0.77 |
| sendrecv_perf | 0.232 | 0.70 |

## What the numbers show

- Only the first two sweeps ran on burst credits: all_reduce and all_gather of repeat 1, which run first. all_reduce rep 1 averages 0.48 GB/s against about 0.21 for the later ones; from the third sweep on, every collective sits at 0.20 to 0.25 GB/s. The pre probe reads about 1.0 GB/s (about 8 Gbit/s), and the post probe is mostly 0.4 to 0.6 GB/s (about 3.5 to 5 Gbit/s).
- That fits the g6.xlarge network burst credits draining during a run (10 Gbit/s burst, 2.5 Gbit/s baseline, per the parser). The result describes the instance's throttled link, not NCCL or the code.
- Use rep 1 or the pre/post probes for a clean read; the 3-repeat mean mixes burst and drained behaviour.

## Against 2026-11-18

| Collective | Avg then -> now | Peak then -> now |
|---|---|---|
| all_gather_perf | 0.248 -> 0.267 | 0.79 -> 0.83 |
| all_reduce_perf | 0.302 -> 0.299 | 0.80 -> 0.79 |
| reduce_scatter_perf | 0.216 -> 0.213 | 0.69 -> 0.77 |
| sendrecv_perf | 0.238 -> 0.232 | 0.75 -> 0.70 |

The means differ by a few percent.

## Source files

`bench/results/net/` (with matching `.log` files):

- `nccl-all_gather_perf-tcp-n2-20261117T120455Z.json`
- `nccl-all_gather_perf-tcp-n2-20261117T131328Z.json`
- `nccl-all_gather_perf-tcp-n2-20261117T142232Z.json`
- `nccl-all_reduce_perf-tcp-n2-20261117T115435Z.json`
- `nccl-all_reduce_perf-tcp-n2-20261117T130203Z.json`
- `nccl-all_reduce_perf-tcp-n2-20261117T141108Z.json`
- `nccl-reduce_scatter_perf-tcp-n2-20261117T121619Z.json`
- `nccl-reduce_scatter_perf-tcp-n2-20261117T132524Z.json`
- `nccl-reduce_scatter_perf-tcp-n2-20261117T143356Z.json`
- `nccl-sendrecv_perf-tcp-n2-20261117T123927Z.json`
- `nccl-sendrecv_perf-tcp-n2-20261117T134800Z.json`
- `nccl-sendrecv_perf-tcp-n2-20261117T145633Z.json`
