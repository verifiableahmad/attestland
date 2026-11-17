#!/usr/bin/env bash
# make pool-move NODE=<name> TO=k8s|slurm
#
# Drain the node in its current scheduler, wait for running work to finish,
# then hand it to the other one. Both schedulers carry the pool: a k8s label
# fleet.attestland/pool=<pool> and a Slurm feature pool_<pool> (Slurm features
# cannot contain "="). The k8s label is the source of truth. Slurm-pool nodes
# also carry the taint fleet.attestland/pool=slurm:NoSchedule: cordon alone
# does not keep DaemonSets or pods that tolerate it off the GPU (D-4).
# Nodes healthd has drained are never moved.
#
# --no-wait (first boot, role initial_pools): refuse instead of waiting when the
# node has running work, never hand over a node that is still busy.
#
# Runs where scontrol and kubectl both work (the control VM); from a laptop it
# re-executes itself there over ssh (AL_CONTROL_HOST).
set -euo pipefail

usage() { echo "usage: $0 <node> <k8s|slurm> [--no-wait] [--timeout SECONDS]" >&2; exit 2; }
[[ $# -ge 2 ]] || usage
node=$1 to=$2; shift 2
wait=1 timeout=${POOL_MOVE_TIMEOUT:-3600}
while [[ $# -gt 0 ]]; do
  case $1 in
    --no-wait) wait=0 ;;
    --timeout) timeout=$2; shift ;;
    *) usage ;;
  esac
  shift
done
[[ $to == k8s || $to == slurm ]] || usage

if ! command -v scontrol >/dev/null 2>&1; then
  host=${AL_CONTROL_HOST:?scontrol not found locally; set AL_CONTROL_HOST to the control VM}
  args=("$node" "$to" --timeout "$timeout")
  [[ $wait == 0 ]] && args+=(--no-wait)
  exec ssh "$host" sudo /opt/al/bin/pool-move "${args[@]}"
fi

log() { printf '%s pool-move %s: %s\n' "$(date -u +%H:%M:%S)" "$node" "$*"; }
deadline=$(( $(date +%s) + timeout ))
TAINT=fleet.attestland/pool

# Refuse to move a node healthd has taken out of service.
reason=$(scontrol show node "$node" -o | grep -o 'Reason=[^ ]*' | cut -d= -f2- || true)
taints=$(kubectl get node "$node" -o jsonpath='{.spec.taints[*].key}' 2>/dev/null || true)
if [[ $reason == healthd:* || $taints == *fleet.attestland/unhealthy* ]]; then
  echo "refusing: $node is drained by healthd (reason=$reason taints=$taints)" >&2
  exit 3
fi

slurm_jobs() { squeue -h -w "$node" -o %i | wc -l | tr -d ' '; }
k8s_job_pods() {
  kubectl get pods -A --field-selector "spec.nodeName=$node,status.phase=Running" \
    -o jsonpath='{range .items[*]}{.metadata.ownerReferences[0].kind}{"\n"}{end}' |
    grep -c '^Job$' || true
}
# wait_idle <what> <counter>: until the counter is 0; with --no-wait, refuse.
wait_idle() {
  while (( $($2) > 0 )); do
    if [[ $wait == 0 ]]; then
      echo "refusing: $node still runs $1 (--no-wait); move it without --no-wait to wait for them" >&2
      exit 5
    fi
    (( $(date +%s) > deadline )) && { echo "timeout waiting for $1 on $node" >&2; exit 4; }
    sleep 10
  done
}

set_kueue_quota() {
  # nvidia.com/gpu quota = GPUs on nodes labelled pool=k8s
  local gpus names idx=-1 i=0 n
  gpus=$(kubectl get nodes -l fleet.attestland/pool=k8s \
    -o jsonpath='{range .items[*]}{.status.capacity.nvidia\.com/gpu}{"\n"}{end}' |
    awk '{s += $1} END {print s + 0}')
  # The quota's index by resource name, not position: reordering
  # flavors-and-queues.yaml must not patch the CPU or memory quota (I-15).
  names=$(kubectl get clusterqueue gpu -o jsonpath='{.spec.resourceGroups[0].flavors[0].resources[*].name}')
  for n in $names; do [[ $n == nvidia.com/gpu ]] && idx=$i; i=$((i + 1)); done
  (( idx >= 0 )) || { echo "clusterqueue gpu has no nvidia.com/gpu quota (resources: $names)" >&2; exit 6; }
  # Kueue's webhook refuses connections for a few seconds after a manager restart.
  for try in 1 2 3 4 5 6; do
    kubectl patch clusterqueue gpu --type=json \
      -p "[{\"op\":\"replace\",\"path\":\"/spec/resourceGroups/0/flavors/0/resources/$idx/nominalQuota\",\"value\":$gpus}]" \
      >/dev/null && break
    (( try < 6 )) || exit 7
    sleep 5
  done
  log "kueue nvidia.com/gpu quota -> $gpus"
}

# --no-wait checks before changing anything, so a refusal leaves the node as it was.
if [[ $wait == 0 ]]; then
  [[ $to == k8s ]] && wait_idle "Slurm jobs" slurm_jobs
  [[ $to == slurm ]] && wait_idle "k8s Jobs" k8s_job_pods
fi

if [[ $to == k8s ]]; then
  log "draining in Slurm"
  scontrol update nodename="$node" state=drain reason="pool=k8s"
  wait_idle "Slurm jobs" slurm_jobs
  scontrol update nodename="$node" availablefeatures=pool_k8s activefeatures=pool_k8s
  kubectl label node "$node" fleet.attestland/pool=k8s --overwrite >/dev/null
  kubectl taint node "$node" "$TAINT-" >/dev/null 2>&1 || true   # no-op if absent
  kubectl uncordon "$node" >/dev/null
  # The quota counts advertised GPUs: wait for the device plugin to report them.
  for _ in $(seq 30); do
    [[ $(kubectl get node "$node" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}') =~ ^[1-9] ]] && break
    sleep 5
  done
  log "now in k8s"
else
  log "cordoning in Kubernetes"
  kubectl cordon "$node" >/dev/null
  kubectl taint node "$node" "$TAINT=slurm:NoSchedule" --overwrite >/dev/null
  wait_idle "k8s Jobs" k8s_job_pods
  kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=10m >/dev/null
  kubectl label node "$node" fleet.attestland/pool=slurm --overwrite >/dev/null
  scontrol update nodename="$node" availablefeatures=pool_slurm activefeatures=pool_slurm
  scontrol update nodename="$node" state=resume 2>/dev/null || true   # no-op if not drained
  log "now in Slurm"
fi
set_kueue_quota
