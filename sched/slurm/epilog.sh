#!/bin/bash
# Slurm epilog (spec 3.1): report XID errors that appeared during the job to healthd.
set -u
node=${SLURMD_NODENAME:-$(hostname -s)}
job=${SLURM_JOB_ID:-0}
base=$(cat "/run/al/xid-$job" 2>/dev/null || echo 0)
lines=$(dmesg 2>/dev/null | grep 'NVRM: Xid' || true)
now=$(printf '%s\n' "$lines" | grep -c 'NVRM: Xid' || true)
rm -f "/run/al/xid-$job"
new=$(( now - base ))
cat > /var/lib/node_exporter/textfile/epilog_xid.prom.$$ <<PROM
# HELP al_epilog_new_xid_total XID lines that appeared during the last job on this node.
# TYPE al_epilog_new_xid_total gauge
al_epilog_new_xid_total{job="$job"} $new
PROM
mv /var/lib/node_exporter/textfile/epilog_xid.prom.$$ /var/lib/node_exporter/textfile/epilog_xid.prom
if (( new > 0 )); then
  xids=$(printf '%s\n' "$lines" | tail -n "$new" | grep -o 'Xid ([^)]*): [0-9]*' | grep -o '[0-9]*$' | sort -u | paste -sd, -)
  curl -fsS -m 5 -H 'Content-Type: application/json' \
    -d "{\"node\":\"$node\",\"source\":\"slurm-epilog\",\"job\":\"$job\",\"xids\":[${xids}],\"count\":$new}" \
    "${HEALTHD_URL:-http://al-control:30082}/v1/events" >/dev/null || true
fi
exit 0
