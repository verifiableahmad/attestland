# Training scaling sweep, AWS 2 x g6.xlarge: 2026-11-18

One `make train-sweep` run (`GPUS="1 2"`): the 124M GPT model trained on 1 GPU
and on 2 GPUs, 3 repeats each, 60M tokens per run. All 6 runs completed. The
pool was destroyed with `make down` right after.

## Summary

- 2 GPUs train 1.46 times as fast as 1 GPU: 53,204 against 36,567 tokens/s,
  a scaling efficiency of 72.7%. The 3 repeats agree within 1.3%.
- The loss to perfect scaling is the network. Each training step sends about
  500 MB between the two nodes, and AWS throttles the g6.xlarge link the whole
  time. Each step takes 1.23 s on 2 GPUs against 0.90 s on 1 GPU.
- Every run starts with about 6 minutes of idle GPU while the node unpacks the
  container image. That is the idle period you see on the dashboards between
  runs, and it adds up to about 37 minutes of the sweep's 3 hours.

## Setup

- 2 x g6.xlarge (1 NVIDIA L4, 4 vCPUs each), us-east-1c, on-demand.
- Container `nvcr.io/nvidia/pytorch:25.10-py3`, started by Pyxis/Enroot, with
  `torchrun` and `torch.compile`.
- Model: 12 layers, 768 wide, 124.5M parameters, 1024-token context.
- Batch: 8 sequences per GPU, 4 gradient accumulation steps, so 32,768 tokens
  per GPU per optimizer step. The per-GPU batch is the same on 1 and 2 GPUs.
- 60M tokens per run: 1,831 steps on 1 GPU, 915 on 2 GPUs. Checkpointing during
  the run was off, and each run saved one final checkpoint.
- Code: git `a4b32e0` plus uncommitted changes, among them the `botocore`,
  `s3transfer` and `jmespath` pins in `workloads/train/requirements-container.txt`.

## Results

| GPUs | Run | Job | Start (UTC) | Length | Tokens/s | Tokens/s per GPU | MFU | Median step |
|---:|---|---:|---|---:|---:|---:|---:|---:|
| 1 | scale-g1-r1 | 31 | 15:35 | 33.5 min | 36,567 | 36,567 | 22.6% | 0.896 s |
| 2 | scale-g2-r1 | 32 | 16:09 | 25.3 min | 53,409 | 26,705 | 16.5% | 1.227 s |
| 1 | scale-g1-r2 | 33 | 16:34 | 33.4 min | 36,696 | 36,696 | 22.7% | 0.893 s |
| 2 | scale-g2-r2 | 34 | 17:08 | 25.4 min | 53,098 | 26,549 | 16.4% | 1.234 s |
| 1 | scale-g1-r3 | 35 | 17:34 | 33.8 min | 36,226 | 36,226 | 22.4% | 0.905 s |
| 2 | scale-g2-r3 | 36 | 18:08 | 25.3 min | 53,204 | 26,602 | 16.4% | 1.232 s |

Medians over the 3 repeats (`scaling-summary-20261118T184559Z.json`):

| GPUs | Tokens/s | Scaling efficiency | MFU |
|---:|---:|---:|---:|
| 1 | 36,567 | 100% | 22.6% |
| 2 | 53,204 | 72.7% | 16.4% |

Scaling efficiency is tokens/s on G GPUs divided by G times the 1-GPU
tokens/s. MFU is the share of the L4's dense bf16 peak (121 TFLOPS) that the
training math uses. "Length" is the whole Slurm job, including the idle start.

## Why the GPUs go idle between runs

The dashboards show long stretches at 100% utilization, then a few minutes at
0%, then 100% again. That is the life of each Slurm job:

| Part of the job | Time | GPU |
|---|---|---|
| Pyxis unpacks the PyTorch image on the node, then `pip` installs the extras | about 5.5 min | 0% |
| `torch.compile` and the first steps | about 40 s | rising |
| Training | 19 min (2 GPUs) or 27 min (1 GPU) | 99 to 100% |
| Final checkpoint, written and uploaded to S3 | about 10 to 30 s | 0% |
| Gap until the sweep submits the next job | 17 to 31 s | 0% |

The idle start was 6.0 to 6.4 minutes in every one of the 6 jobs. Pyxis
unpacks the image again for every job, even on a node that already ran it. The
GPUs were idle for about 37 minutes of the sweep's 3 hours (15:35 to 18:34),
about 20%.

Before the sweep, from 15:22 to 15:34, the GPUs were idle for the same reason:
jobs 29 and 30 spent nearly all their time unpacking the image. Job 29 was cancelled
and job 30 crashed at startup (see "Problems during the run").

## Why 2 GPUs are not twice as fast

On 1 GPU nothing crosses the network. On 2 GPUs, each node holds a full copy of
the model, and after every optimizer step the two nodes add up their gradients
over the network (NCCL all_reduce). The measurements during training, averaged
over each run after its first 2 minutes:

| | 1 GPU | 2 GPUs (each node) |
|---|---:|---:|
| GPU utilization | 100% | 99 to 100% |
| Tensor core activity | 56% | 27 to 29% |
| Network received / sent | 0 | about 406 / 407 MB/s |
| Received per step | 0 | about 500 MB |
| AWS throttle counter, inbound (per minute) | 0 | 224,000 to 248,000 |
| AWS throttle counter, outbound (per minute) | 0 | 559,000 to 618,000 |
| Median step time | 0.90 s | 1.23 s |

What this shows:

- The 500 MB per step matches the size of the model's gradients in 32-bit
  floats (124.5M parameters x 4 bytes = 498 MB). With 2 nodes, each node sends
  and receives about one full copy of the gradients per step.
- About 406 MB/s is about 3.3 Gbit/s each way. The throttle counters
  (`bw_in_allowance_exceeded` and `bw_out_allowance_exceeded` from the ENA
  driver) count packets that AWS held back or dropped because the instance went
  over its bandwidth allowance. They rose all through every 2-GPU run and stayed
  at 0 in the 1-GPU runs, so the link ran at its limit the whole time.
- A step on 2 GPUs takes 0.33 s longer than on 1 GPU. At 406 MB/s, moving
  500 MB takes about 1.2 s, so most of the transfer must overlap with the
  backward pass. The extra 0.33 s is the part the GPUs spend waiting. This is a
  reading of the numbers above, not a separate measurement.
- "GPU utilization" stays near 100% on 2 GPUs even while the GPUs wait, because
  DCGM counts any time a kernel is running, and NCCL's kernels sit on the GPU
  while they wait for data. Tensor core activity (`DCGM_FI_PROF_PIPE_TENSOR_ACTIVE`)
  or `al_train_mfu` shows the real work: it halves, from 56% to about 28%.

The numbers were the same in all 3 repeats: the link got neither faster nor
slower from one run to the next. The NCCL runs of the same day are in
`nccl-aws-g6-tcp-n2-2026-11-18.md`.

## Problems during the run

- **Missing `botocore` in the container.** `train.sbatch` installs
  `requirements-container.txt` with `pip install --no-deps`, so `boto3` came
  without its own dependencies and every run crashed at startup (job 30). Fixed
  by pinning `botocore==1.40.30`, `s3transfer==0.14.0` and `jmespath==1.1.0`.
- **Ctrl-C left a job running.** The first `make train-sweep` was stopped with
  Ctrl-C; its job (29) kept running and holding a node until it was cancelled
  with `scancel 29`. The runbook now says so.
- **The summary step failed on the cluster.** `scaling.py` reads
  `AL_RESULTS_DIR`, which is set only for login shells (`/etc/profile.d/al.sh`).
  The sweep was started over a plain `ssh host 'make ...'`, so it looked in the
  empty `/opt/al/repo/bench/results` and stopped with "need a 1-GPU baseline".
  The training runs were not affected, because `train.sbatch` sets the variable
  itself. The summary was then made on the laptop from the pulled files. Start
  the sweep from `make control-ssh`, which is a login shell, to avoid this.

## What would change the numbers

None of these were tried.

- **Idle start:** unpack the image once per node into a file on `/scratch` and
  give Pyxis that file, which saves about 5 minutes per job.
- **Network:** send the gradients in bf16 instead of fp32, which halves the
  500 MB per step. Or accumulate more micro-batches per step, so the gradients
  cross the network less often per token. Or use g6.2xlarge, whose network
  allowance is twice as high (5 Gbit/s baseline), as ADR 010 planned.

## Files

- `bench/results/train/scale-g{1,2}-r{1,2,3}-*.json`: one result file per run.
- `bench/results/train/scaling-summary-20261118T184559Z.json`: the medians and
  scaling efficiency.
- `bench/results/train/metrics-aws-g6-20261118.json`: GPU, tensor core,
  network, throttle counter and training metrics for both nodes, 15:20 to 18:36
  UTC every 15 s, exported from Prometheus before `make down`.
- `bench/results/logs/aws/`: the Slurm job logs.
- Checkpoints, one per run: `s3://<prefix>-checkpoints/scale-g1-r{1,2,3}/step_00001831/`
  and `s3://<prefix>-checkpoints/scale-g2-r{1,2,3}/step_00000915/`.
