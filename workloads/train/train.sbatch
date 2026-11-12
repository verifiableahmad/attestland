#!/bin/bash
# Multi-node containerised training (spec 3.1): Pyxis/Enroot + torchrun.
#   sbatch -N 2 --gpus-per-node=1 workloads/train/train.sbatch [config] [--set k=v ...]
#SBATCH --job-name=gpt124m
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --exclusive
#SBATCH --requeue
#SBATCH --signal=USR1@120
#SBATCH --open-mode=append
#SBATCH --output=/scratch/logs/train-%j.log
set -euo pipefail
source /etc/fleet/versions.env
REPO=${AL_REPO:-/opt/al/repo}
CONFIG=${1:-$REPO/workloads/train/configs/gpt124m.yaml}; shift || true
head=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -1)
export MASTER_ADDR=$head MASTER_PORT=29500
export TORCH_NCCL_ASYNC_ERROR_HANDLING=1 NCCL_DEBUG=WARN
export NCCL_SOCKET_IFNAME=${NCCL_SOCKET_IFNAME:-$(ip -o -4 route show to default | awk '{print $5}' | head -1)}
set -a; source /etc/fleet/s3.env; set +a   # AL_S3_REGION, AL_BUCKET_PREFIX (credentials: the node role)
echo "$(date -u +%FT%TZ) job=$SLURM_JOB_ID restart=${SLURM_RESTART_COUNT:-0} nodes=$SLURM_JOB_NODELIST"

srun --container-image="$PYTORCH_IMAGE" \
     --container-mounts=/scratch:/scratch,/var/lib/node_exporter/textfile:/var/lib/node_exporter/textfile,$REPO:$REPO \
     --container-workdir="$REPO" \
     --export=ALL \
     bash -c "pip install -q --no-deps -r $REPO/workloads/train/requirements-container.txt && \
              torchrun --nnodes=$SLURM_NNODES --nproc-per-node=$SLURM_GPUS_PER_NODE \
                --rdzv-backend=c10d --rdzv-endpoint=$head:29500 --rdzv-id=$SLURM_JOB_ID \
                $REPO/workloads/train/train.py --config $CONFIG --set $*"
