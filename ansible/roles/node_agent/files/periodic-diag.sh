#!/bin/bash
# dcgmi diag -r 1 -> node_exporter textfile. healthd reads al_dcgm_diag_pass.
# Skipped while the node has work: the diag would compete with a job for the
# GPU, and a diag that fails only because the GPU is busy would count towards
# draining the node (D-5). A skip leaves the last result in place.
set -u
out=/var/lib/node_exporter/textfile/dcgm_diag.prom
node=$(hostname -s)
busy=""
state=$(scontrol show node "$node" -o 2>/dev/null | grep -o 'State=[^ ]*' | cut -d= -f2 || true)
[[ $state == *ALLOC* || $state == *MIX* || $state == *COMPLETING* ]] && busy="slurm:$state"
apps=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -c '[0-9]' || true)
(( apps > 0 )) && busy="${busy:+$busy,}compute_apps:$apps"
if [[ -n $busy ]]; then
  cat > "$out.skip.$$" <<PROM
# HELP al_dcgm_diag_skipped_timestamp_seconds Unix time of the last skipped diag (node busy).
# TYPE al_dcgm_diag_skipped_timestamp_seconds gauge
al_dcgm_diag_skipped_timestamp_seconds{level="1"} $(date +%s)
PROM
  mv "$out.skip.$$" /var/lib/node_exporter/textfile/dcgm_diag_skip.prom
  echo "skipped: busy ($busy)"
  exit 0
fi
start=$(date +%s)
if timeout 300 dcgmi diag -r 1 -j > /run/al-diag.json 2>&1; then pass=1; else pass=0; fi
# dcgmi exits 0 even with a failed test in some versions: also scan the JSON.
grep -q '"status" *: *"Fail"' /run/al-diag.json 2>/dev/null && pass=0
cat > "$out.$$" <<PROM
# HELP al_dcgm_diag_pass 1 if the last dcgmi diag -r 1 passed.
# TYPE al_dcgm_diag_pass gauge
al_dcgm_diag_pass{level="1"} $pass
# HELP al_dcgm_diag_last_run_timestamp_seconds Unix time of the last diag.
# TYPE al_dcgm_diag_last_run_timestamp_seconds gauge
al_dcgm_diag_last_run_timestamp_seconds{level="1"} $start
PROM
mv "$out.$$" "$out"
