#!/bin/bash
# One nccl-tests sweep (spec 2.2): 8 B -> 8 GB, powers of two.
# Usage: sbatch -N <nodes> [--gpus-per-node=<n>] bench/nccl/nccl.sbatch <collective> <transport: intra|tcp|rdma>
#   intra: one node, all of its GPUs in one process (run-all.sh passes --gpus-per-node)
#   MAX_BYTES=64M: a short sweep (make nccl-smoke)
# Beside the log: nccl-<job>.hw.jsonl (each node's hardware, for the result file)
# and, for tcp and rdma, nccl-<job>.probe-{pre,post}.log, a fixed-size all_reduce
# before and after the sweep that shows whether the link changed during it (NCCL-8).
#SBATCH --job-name=nccl
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --exclusive
# A default for manual runs; run-all.sh passes --time (JOB_MIN).
#SBATCH --time=00:30:00
#SBATCH --output=/scratch/logs/nccl-%j.log
set -euo pipefail
coll=${1:?collective, e.g. all_reduce_perf}
transport=${2:?intra|tcp|rdma}
REPO=${AL_REPO:-/opt/al/repo}
out=/scratch/logs/nccl-$SLURM_JOB_ID
gpus_per_node=$(nvidia-smi -L | wc -l)
# The parser runs on the control node, so the hardware comes from here.
srun --ntasks-per-node=1 python3 "$REPO/bench/lib/result.py" hardware > "$out.hw.jsonl"
export NCCL_DEBUG=INFO
export NCCL_DEBUG_SUBSYS=INIT,NET
net_env() {
  export NCCL_SOCKET_IFNAME=${NCCL_SOCKET_IFNAME:-$(ip -o -4 route show to default | awk '{print $5}' | head -1)}
  # nccl-tests is built with MPI=1: OpenMPI exchanges NCCL's id and times the
  # runs. Keep it on the same interface; the GPU nodes are k8s workers too, and
  # left alone it may pick a Calico interface and hang (NCCL-6).
  export OMPI_MCA_pml=ob1 OMPI_MCA_btl=self,vader,tcp OMPI_MCA_btl_tcp_if_include=$NCCL_SOCKET_IFNAME
}
case $transport in
  intra)
    # single node: NVLink/PCIe/SHM, all local GPUs in one process
    (( gpus_per_node >= 2 )) || { echo "SKIP: intra needs 2+ GPUs on a node, this job has $gpus_per_node"; exit 0; }
    ;;
  tcp)
    net_env
    export NCCL_IB_DISABLE=1
    export NCCL_SOCKET_NTHREADS=${NCCL_SOCKET_NTHREADS:-4}
    export NCCL_NSOCKS_PERTHREAD=${NCCL_NSOCKS_PERTHREAD:-4}
    ;;
  rdma)
    [[ -d /sys/class/infiniband ]] && compgen -G '/sys/class/infiniband/*' >/dev/null ||
      { echo "SKIP: no RDMA devices on this instance type"; exit 0; }
    net_env
    efa=0
    for d in /sys/class/infiniband/*; do
      [[ $(basename "$(readlink -f "$d/device/driver")") == efa ]] && efa=1
    done
    if (( efa )); then
      # AWS EFA (g6.8xlarge and larger): NCCL reaches it through the aws-ofi-nccl
      # plugin (libfabric), not IB verbs. The roles do not install the plugin yet.
      plugin=$(ls /opt/amazon/ofi-nccl/lib/libnccl-net.so /opt/aws-ofi-nccl/lib/libnccl-net.so 2>/dev/null | head -1 || true)
      [[ -n $plugin ]] || { echo "SKIP: EFA devices but no aws-ofi-nccl plugin (libnccl-net.so)"; exit 0; }
      export LD_LIBRARY_PATH=$(dirname "$plugin")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
      export FI_PROVIDER=efa FI_EFA_USE_DEVICE_RDMA=1
      echo "AL rdma=efa plugin=$plugin"
    else
      export NCCL_IB_HCA=${NCCL_IB_HCA:-$(ls /sys/class/infiniband | paste -sd,)}
      export NCCL_IB_GID_INDEX=${NCCL_IB_GID_INDEX:-3}
      echo "AL rdma=ib"
    fi
    ;;
esac
if [[ $transport == intra ]]; then
  srun -N 1 -n 1 /opt/nccl-tests/build/"$coll" -b 8 -e "${MAX_BYTES:-8G}" -f 2 -g "$gpus_per_node" -w 5 -n 20
else
  probe() { srun --mpi=pmix /opt/nccl-tests/build/all_reduce_perf -b 64M -e 64M -g 1 -w 2 -n 10 > "$out.probe-$1.log" 2>&1; }
  probe pre
  srun --mpi=pmix /opt/nccl-tests/build/"$coll" -b 8 -e "${MAX_BYTES:-8G}" -f 2 -g 1 -w 5 -n 20
  probe post
fi
