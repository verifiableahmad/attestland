#!/usr/bin/env bash
# make chaos FAULT=<name> TARGET=<node> [ARGS...]
#
# Faults (spec 6.2):
#   node-loss    stop the VM through the provider API (AWS)       revert: start it
#   rank-crash   kill -9 the training process on the node
#   net-delay    tc netem delay on the data NIC (DELAY=5ms|50ms)   revert: remove qdisc
#   pkt-loss     tc netem loss (LOSS=0.1%|1%)                      revert: remove qdisc
#   slow-gpu     lock GPU clocks low (CLOCK_MHZ, default 600)      revert: reset clocks
#   disk-full    fill /scratch with fallocate                       revert: delete the file
#   gpu-fault    DCGM XID injection (XID=79 default)               (healthd drains)
# Use `--revert` as the third argument to undo a degradation fault.
# Every injection appends {t, fault, target, action} to chaos/log/injections.jsonl.
set -euo pipefail
fault=${1:?fault} target=${2:?target node} action=${3:-inject}
HERE=$(cd "$(dirname "$0")" && pwd)
LOG=${CHAOS_LOG:-$HERE/log/injections.jsonl}
mkdir -p "$(dirname "$LOG")"
SSH=${CHAOS_SSH:-ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10}
REGION=${AWS_REGION:-us-east-1}
# EC2 instance id of the target node (instances carry Name=<node>).
instance_id() {
  aws ec2 describe-instances --region "$REGION" --output text \
    --filters "Name=tag:Name,Values=$target" Name=tag:project,Values=attestland \
              Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[0].Instances[0].InstanceId'
}
remote() { $SSH "$target" sudo bash -c "$(printf '%q ' "$@")"; }
record() {
  printf '{"t": %s, "fault": "%s", "target": "%s", "action": "%s", "detail": "%s"}\n' \
    "$(date +%s.%N)" "$fault" "$target" "$action" "${1:-}" >> "$LOG"
}
iface='$(ip -o -4 route show to default | awk "{print \$5}" | head -1)'
# Simulated repair for the experiment: hand the node back to Slurm and clear
# healthd's taint. Real repairs go through burn-in (fleet/burnin) instead.
repair() {
  scontrol update nodename="$target" state=resume 2>/dev/null || true
  kubectl taint node "$target" fleet.attestland/unhealthy- 2>/dev/null || true
  record "simulated repair"
}

case "$fault/$action" in
  node-loss/inject)   record "ec2 stop"; aws ec2 stop-instances --region "$REGION" --instance-ids "$(instance_id)" >/dev/null ;;
  node-loss/--revert) id=$(instance_id); aws ec2 start-instances --region "$REGION" --instance-ids "$id" >/dev/null
                      aws ec2 wait instance-running --region "$REGION" --instance-ids "$id"; record "ec2 start"; sleep 60; repair ;;
  rank-crash/inject)  record "kill -9 train.py"; remote pkill -9 -f 'workloads/train/train.py' ;;
  net-delay/inject)   record "netem delay ${DELAY:-5ms}"; $SSH "$target" "sudo tc qdisc replace dev $iface root netem delay ${DELAY:-5ms}" ;;
  pkt-loss/inject)    record "netem loss ${LOSS:-0.1%}"; $SSH "$target" "sudo tc qdisc replace dev $iface root netem loss ${LOSS:-0.1%}" ;;
  net-delay/--revert|pkt-loss/--revert)
                      $SSH "$target" "sudo tc qdisc del dev $iface root 2>/dev/null || true"; record "netem removed" ;;
  slow-gpu/inject)    record "lock clocks ${CLOCK_MHZ:-600}MHz"; remote nvidia-smi -lgc "${CLOCK_MHZ:-600},${CLOCK_MHZ:-600}" ;;
  slow-gpu/--revert)  remote nvidia-smi -rgc; record "clocks reset"; repair ;;
  disk-full/inject)
    record "fallocate /scratch"
    $SSH "$target" 'sudo bash -c "avail=\$(df --output=avail -B1 /scratch | tail -1); fallocate -l \$((avail - 64*1024*1024)) /scratch/.chaos-fill"' ;;
  disk-full/--revert) remote rm -f /scratch/.chaos-fill; record "fill removed" ;;
  gpu-fault/inject)
    # An injected DCGM field does not break the GPU, but a real XID 79 kills the
    # CUDA context, so the training rank on that node is killed too.
    record "dcgm inject XID ${XID:-79} + kill rank"
    remote /opt/al/repo/fleet/healthd/test/inject-xid.sh 0 "${XID:-79}"
    remote pkill -9 -f 'workloads/train/train.py' || true ;;
  gpu-fault/--revert) repair ;;
  *) echo "unknown fault/action: $fault/$action" >&2; exit 2 ;;
esac
echo "$fault $action on $target"
