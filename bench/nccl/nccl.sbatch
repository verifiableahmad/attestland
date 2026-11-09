#!/bin/bash
# One nccl-tests sweep (spec 2.2): 8 B -> 8 GB, powers of two.
# Usage: sbatch -N <nodes> bench/nccl/nccl.sbatch <collective> <transport: intra|tcp|rdma>
#SBATCH --job-name=nccl
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/logs/nccl-%j.log
set -euo pipefail
coll=${1:?collective, e.g. all_reduce_perf}
transport=${2:?intra|tcp|rdma}
gpus_per_node=$(nvidia-smi -L | wc -l)
export NCCL_DEBUG=INFO
export NCCL_DEBUG_SUBSYS=INIT,NET
case $transport in
  intra) ;;  # single node: NVLink/PCIe/SHM, all local GPUs in one process
  tcp)
    export NCCL_IB_DISABLE=1
    export NCCL_SOCKET_IFNAME=${NCCL_SOCKET_IFNAME:-$(ip -o -4 route show to default | awk '{print $5}' | head -1)}
    export NCCL_SOCKET_NTHREADS=${NCCL_SOCKET_NTHREADS:-4}
    export NCCL_NSOCKS_PERTHREAD=${NCCL_NSOCKS_PERTHREAD:-4}
    ;;
  rdma)
    [[ -d /sys/class/infiniband ]] || { echo "SKIP: no RDMA devices on this instance type"; exit 0; }
    export NCCL_IB_HCA=${NCCL_IB_HCA:-$(ls /sys/class/infiniband | paste -sd,)}
    export NCCL_IB_GID_INDEX=${NCCL_IB_GID_INDEX:-3}
    ;;
esac
if [[ $transport == intra ]]; then
  srun -N 1 -n 1 /opt/nccl-tests/build/"$coll" -b 8 -e 8G -f 2 -g "$gpus_per_node" -w 5 -n 20
else
  srun --mpi=pmix /opt/nccl-tests/build/"$coll" -b 8 -e 8G -f 2 -g 1 -w 5 -n 20
fi
