#!/bin/bash
# make ckpt-bench (spec 5.3): checkpoint stall, time to durable and restore time
# per size and save mode, in the training container on one GPU node. Writes
# bench/results/ckpt/ckpt-*.json under AL_RESULTS_DIR (the control node's share).
#   sbatch storage/ckpt_bench.sbatch            sizes that fit this node's memory
#   SIZES=model,10G,50G sbatch storage/ckpt_bench.sbatch
#SBATCH --job-name=ckpt-bench
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/logs/ckpt-bench-%j.log
set -euo pipefail
source /etc/fleet/versions.env
REPO=${AL_REPO:-/opt/al/repo}
set -a; source /etc/fleet/s3.env; set +a   # AL_S3_REGION, AL_BUCKET_PREFIX (credentials: the node role)
export AL_RESULTS_DIR=${AL_RESULTS_DIR:-/opt/al/results}
if [[ -z ${SIZES:-} ]]; then
  # The benchmark holds a state and a same-size restore target in host memory:
  # keep the spec's sizes (10G, 50G) that fit twice with room to spare.
  mem_g=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
  SIZES=model
  for s in 10 50; do (( 2 * s + 4 <= mem_g )) && SIZES+=",${s}G"; done
  echo "sizes for ${mem_g} GiB of memory: $SIZES (SIZES= to override)"
fi
srun --container-image="${PYTORCH_IMAGE/\//#}" \
     --container-mounts=/scratch:/scratch,$REPO:$REPO,$AL_RESULTS_DIR:$AL_RESULTS_DIR \
     --container-workdir="$REPO" \
     --export=ALL \
     bash -c "pip install -q --no-deps -r $REPO/workloads/train/requirements-container.txt && \
              python $REPO/storage/ckpt_bench.py --local /scratch/ckpt-bench --durable s3://checkpoints/bench \
                --sizes $SIZES --repeats ${REPEATS:-3}"
