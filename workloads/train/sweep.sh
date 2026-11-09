#!/usr/bin/env bash
# make train-sweep: 1, 2 and 4 GPUs at the same per-GPU batch, 3 repeats each,
# a fixed shorter budget for throughput (the full budget is for goodput runs).
set -euo pipefail
cd "$(dirname "$0")/../.."
BUDGET=${BUDGET:-60000000}
for rep in 1 2 3; do
  for n in 1 2 4; do
    sbatch --wait -N "$n" workloads/train/train.sbatch workloads/train/configs/gpt124m.yaml \
      "run_name=scale-g$n-r$rep" "run_dir=/scratch/runs/scale-g$n-r$rep" "train.token_budget=$BUDGET" \
      "checkpoint.interval_s=1e9"
  done
done
.venv/bin/python workloads/train/scaling.py
