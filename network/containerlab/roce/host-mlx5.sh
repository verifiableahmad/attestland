#!/usr/bin/env bash
# Host side of the lossless class for an mlx5 (ConnectX-6/7) backend NIC.
# Hardware only; not run in the lab. Usage: host-mlx5.sh <netdev> <rdma-dev>
set -euo pipefail
dev=${1:?netdev, e.g. ens1np0}
rdma=${2:?rdma device, e.g. mlx5_0}

# Trust DSCP, PFC on priority 3 only
mlnx_qos -i "$dev" --trust dscp
mlnx_qos -i "$dev" --pfc 0,0,0,1,0,0,0,0

# RoCE traffic marked DSCP 26 (ToS 104), CNPs DSCP 48
cma_roce_tos -d "$rdma" -t 104
echo 48 > "/sys/class/net/$dev/ecn/roce_np/cnp_dscp"

# DCQCN reaction point and notification point on priority 3
echo 1 > "/sys/class/net/$dev/ecn/roce_rp/enable/3"
echo 1 > "/sys/class/net/$dev/ecn/roce_np/enable/3"

# NCCL side: export NCCL_IB_TC=104 and NCCL_IB_GID_INDEX=3 (RoCEv2) in the job env.
