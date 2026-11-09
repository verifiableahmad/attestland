#!/bin/bash
# Slurm prolog (spec 3.1): fast GPU sanity check before a job starts.
# A non-zero exit drains this node with the reason below and requeues the job.
set -u
node=${SLURMD_NODENAME:-$(hostname -s)}
fail() {
  /usr/bin/scontrol update nodename="$node" state=drain reason="prolog:$1" 2>/dev/null
  echo "prolog failed on $node: $1" >&2
  exit 1
}
expected=$(/usr/bin/scontrol show node "$node" -o | grep -o 'Gres=gpu:[^ ]*' | grep -o '[0-9]*$')
out=$(timeout 20 nvidia-smi --query-gpu=index --format=csv,noheader 2>&1) || fail "nvidia-smi:$(echo "$out" | head -c 80)"
found=$(echo "$out" | grep -c '^[0-9]')
[[ -n "$expected" && "$found" -ne "$expected" ]] && fail "gpu_count:$found/$expected"
# Baseline XID count for the epilog.
mkdir -p /run/al
dmesg 2>/dev/null | grep -c 'NVRM: Xid' > "/run/al/xid-${SLURM_JOB_ID:-0}" || true
exit 0
