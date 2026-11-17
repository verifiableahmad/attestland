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
# Gres=gpu:1, or gpu:<type>:1(S:0) once NVML names the type: the count is the
# number before any "(" (I-12). An unparsable string fails here, not silently.
gres=$(/usr/bin/scontrol show node "$node" -o | grep -o 'Gres=[^ ]*')
expected=$(sed -nE 's/.*Gres=gpu:([^:( ]*:)?([0-9]+).*/\2/p' <<<"$gres")
[[ -n $expected ]] || fail "gres_unparsed:${gres:-none}"
out=$(timeout 20 nvidia-smi --query-gpu=index --format=csv,noheader 2>&1) || fail "nvidia-smi:$(echo "$out" | head -c 80)"
found=$(echo "$out" | grep -c '^[0-9]')
(( found == expected )) || fail "gpu_count:$found/$expected"
# With an instance store, /scratch must be it: after a stop/start the boot units
# rebuild it (role scratch); if they did not, jobs would fill the root disk (D-9).
if compgen -G '/dev/disk/by-id/nvme-Amazon_EC2_NVMe_Instance_Storage_*' >/dev/null; then
  mountpoint -q /scratch || fail "scratch_not_mounted"
fi
# The control node's shares are automounts (role shared): mount them here, on
# the host, before a container bind-mounts /scratch or the repo and would see an
# empty mount point. Unreachable control node: no job can write its log anyway.
for d in /opt/al/repo /opt/al/results /scratch/logs /scratch/runs; do
  [[ -d $d ]] || continue
  timeout 30 stat -t "$d/." >/dev/null 2>&1 || fail "nfs:$d"
done
# Job start for the epilog's XID count (journal time, not a dmesg line count).
mkdir -p /run/al
date +%s > "/run/al/start-${SLURM_JOB_ID:-0}"
exit 0
