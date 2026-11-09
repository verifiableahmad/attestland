#!/usr/bin/env bash
# make pool-move NODE=<name> TO=k8s|slurm
#
# Drain the node in its current scheduler, wait for running work to finish,
# then hand it to the other one. Both schedulers carry the pool: a k8s label
# fleet.attestland/pool=<pool> and a Slurm feature pool_<pool> (Slurm features
# cannot contain "="). Nodes healthd has drained are never moved.
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

set_kueue_quota() {
  # nvidia.com/gpu quota = GPUs on nodes labelled pool=k8s
  local gpus
  gpus=$(kubectl get nodes -l fleet.attestland/pool=k8s \
    -o jsonpath='{range .items[*]}{.status.capacity.nvidia\.com/gpu}{"\n"}{end}' |
    awk '{s += $1} END {print s + 0}')
  kubectl patch clusterqueue gpu --type=json \
    -p "[{\"op\":\"replace\",\"path\":\"/spec/resourceGroups/0/flavors/0/resources/2/nominalQuota\",\"value\":$gpus}]" >/dev/null
  log "kueue nvidia.com/gpu quota -> $gpus"
}

if [[ $to == k8s ]]; then
  log "draining in Slurm"
  scontrol update nodename="$node" state=drain reason="pool=k8s"
  if [[ $wait == 1 ]]; then
    while (( $(slurm_jobs) > 0 )); do
      (( $(date +%s) > deadline )) && { echo "timeout waiting for Slurm jobs on $node" >&2; exit 4; }
      sleep 10
    done
  fi
  scontrol update nodename="$node" availablefeatures=pool_k8s activefeatures=pool_k8s
  kubectl label node "$node" fleet.attestland/pool=k8s --overwrite >/dev/null
  kubectl uncordon "$node" >/dev/null
  log "now in k8s"
else
  log "cordoning in Kubernetes"
  kubectl cordon "$node" >/dev/null
  if [[ $wait == 1 ]]; then
    while (( $(k8s_job_pods) > 0 )); do
      (( $(date +%s) > deadline )) && { echo "timeout waiting for k8s Jobs on $node" >&2; exit 4; }
      sleep 10
    done
  fi
  kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=10m >/dev/null
  kubectl label node "$node" fleet.attestland/pool=slurm --overwrite >/dev/null
  scontrol update nodename="$node" availablefeatures=pool_slurm activefeatures=pool_slurm
  scontrol update nodename="$node" state=resume 2>/dev/null || true   # no-op if not drained
  log "now in Slurm"
fi
set_kueue_quota
