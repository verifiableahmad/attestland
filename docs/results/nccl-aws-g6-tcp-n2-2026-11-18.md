# NCCL over TCP, 2 nodes, AWS g6.xlarge: 2026-11-18

One `make nccl` run: 4 collectives x 3 repeats, 2 ranks (1 L4 GPU per node) over the NCCL socket transport. The pool was rebuilt between the two runs (al-control had been up 1h05 at 08:11 UTC on 11-18); the sweeps ran from 11:45 to 14:48 UTC.

## Setup

- Instances: 2 x g6.xlarge (NVIDIA L4), us-east-1; nodes al-gpu-01, al-gpu-02
- Software: NCCL 2.27.7-1+cuda12.9, CUDA 12.9.1, driver 580.196.07, kernel 6.8.0-1069-aws
- Transport check: `NET/Socket` on both ranks
- Code: git `a4b32e013f70510b15f1be936bdf749ce4a48792-dirty`
- Sweep: 30 message sizes per file, x2 steps, 5 warmup + 20 timed iterations. The requested range is 8 B to 8 GB; nccl-tests lowered the top to 6.34 GiB when the buffers did not fit.
- Marked not final by the parser: burstable network, 2.5 Gbps baseline / 10 Gbps burst (ADR 010 assumes g6.2xlarge)

## Results (bus bandwidth, GB/s)

Units are gigabytes per second, as nccl-tests prints them (the JSON fields are named `*_gbps` but hold GB/s; x8 for Gbit/s). `avg` is the mean over all sizes, `peak` the best size. `probe pre/post` is a 64 MiB all_reduce before and after the sweep.

| Collective | Rep | Start (UTC) | Avg | Peak | Probe pre | Probe post |
|---|---:|---|---:|---:|---:|---:|
| all_gather_perf | 1 | 11:56 | 0.314 | 0.99 | 1.07 | 0.42 |
| all_gather_perf | 2 | 13:05 | 0.212 | 0.65 | 1.06 | 0.44 |
| all_gather_perf | 3 | 14:13 | 0.218 | 0.74 | 1.07 | 0.42 |
| all_reduce_perf | 1 | 11:45 | 0.484 | 1.08 | 1.02 | 1.10 |
| all_reduce_perf | 2 | 12:53 | 0.209 | 0.67 | 1.02 | 0.44 |
| all_reduce_perf | 3 | 14:02 | 0.214 | 0.66 | 1.04 | 0.42 |
| reduce_scatter_perf | 1 | 12:07 | 0.223 | 0.85 | 1.06 | 0.46 |
| reduce_scatter_perf | 2 | 13:16 | 0.212 | 0.61 | 1.06 | 0.52 |
| reduce_scatter_perf | 3 | 14:25 | 0.214 | 0.61 | 1.00 | 0.42 |
| sendrecv_perf | 1 | 12:30 | 0.233 | 0.67 | 1.07 | 0.44 |
| sendrecv_perf | 2 | 13:39 | 0.232 | 0.69 | 1.01 | 0.59 |
| sendrecv_perf | 3 | 14:48 | 0.248 | 0.88 | 0.98 | 0.48 |

Mean over the 3 repeats:

| Collective | Avg | Peak |
|---|---:|---:|
| all_gather_perf | 0.248 | 0.79 |
| all_reduce_perf | 0.302 | 0.80 |
| reduce_scatter_perf | 0.216 | 0.69 |
| sendrecv_perf | 0.238 | 0.75 |

## What the numbers show

- Only the first two sweeps ran on burst credits: all_reduce and all_gather of repeat 1, which run first. all_reduce rep 1 averages 0.48 GB/s against about 0.21 for the later ones; from the third sweep on, every collective sits at 0.20 to 0.25 GB/s. The pre probe reads about 1.0 GB/s (about 8 Gbit/s), and the post probe is mostly 0.4 to 0.6 GB/s (about 3.5 to 5 Gbit/s).
- That fits the g6.xlarge network burst credits draining during a run (10 Gbit/s burst, 2.5 Gbit/s baseline, per the parser). The result describes the instance's throttled link, not NCCL or the code.
- Use rep 1 or the pre/post probes for a clean read; the 3-repeat mean mixes burst and drained behaviour.

## Against 2026-11-17

| Collective | Avg then -> now | Peak then -> now |
|---|---|---|
| all_gather_perf | 0.267 -> 0.248 | 0.83 -> 0.79 |
| all_reduce_perf | 0.299 -> 0.302 | 0.79 -> 0.80 |
| reduce_scatter_perf | 0.213 -> 0.216 | 0.77 -> 0.69 |
| sendrecv_perf | 0.232 -> 0.238 | 0.70 -> 0.75 |

The means differ by a few percent.

## Source files

`bench/results/net/` (with matching `.log` files):

- `nccl-all_gather_perf-tcp-n2-20261118T115621Z.json`
- `nccl-all_gather_perf-tcp-n2-20261118T130525Z.json`
- `nccl-all_gather_perf-tcp-n2-20261118T141358Z.json`
- `nccl-all_reduce_perf-tcp-n2-20261118T114528Z.json`
- `nccl-all_reduce_perf-tcp-n2-20261118T125329Z.json`
- `nccl-all_reduce_perf-tcp-n2-20261118T140234Z.json`
- `nccl-reduce_scatter_perf-tcp-n2-20261118T120745Z.json`
- `nccl-reduce_scatter_perf-tcp-n2-20261118T131650Z.json`
- `nccl-reduce_scatter_perf-tcp-n2-20261118T142522Z.json`
- `nccl-sendrecv_perf-tcp-n2-20261118T123053Z.json`
- `nccl-sendrecv_perf-tcp-n2-20261118T133926Z.json`
- `nccl-sendrecv_perf-tcp-n2-20261118T144830Z.json`
