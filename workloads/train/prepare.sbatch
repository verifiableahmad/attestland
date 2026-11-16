#!/bin/bash
# Training corpus (spec 3.1), in the training container, which has what the
# tokeniser needs: the first node builds the shards and uploads them unless the
# bucket already holds them, then every node of the job copies to its /scratch
# what it lacks. Run it again after pool-move or a node replacement.
#   sbatch -N <nodes> workloads/train/prepare.sbatch
#SBATCH --job-name=prepare
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --output=/scratch/logs/prepare-%j.log
set -euo pipefail
source /etc/fleet/versions.env
REPO=${AL_REPO:-/opt/al/repo}
SOURCE=${SOURCE:-hf:HuggingFaceFW/fineweb-edu:sample-10BT}
TOKENS=${TOKENS:-2_000_000_000}
NAME=${NAME:-fineweb-edu-2b}
set -a; source /etc/fleet/s3.env; set +a   # AL_S3_REGION, AL_BUCKET_PREFIX (credentials: the node role)

prepare() {  # prepare <tasks> <prepare.py arguments>
  local n=$1; shift
  srun -N "$n" -n "$n" --container-image="$PYTORCH_IMAGE" \
       --container-mounts=/scratch:/scratch,$REPO:$REPO \
       --container-workdir="$REPO" \
       --export=ALL \
       bash -c "pip install -q -r $REPO/workloads/train/requirements-prepare.txt && \
                python $REPO/workloads/train/prepare.py $*"
}
prepare 1 --source "$SOURCE" --tokens "$TOKENS" --out "/scratch/data/$NAME" --upload "s3://datasets/$NAME" --if-missing
prepare "$SLURM_NNODES" --fetch "s3://datasets/$NAME" --out "/scratch/data/$NAME"
