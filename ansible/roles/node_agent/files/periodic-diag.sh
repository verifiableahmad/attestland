#!/bin/bash
# dcgmi diag -r 1 -> node_exporter textfile. healthd reads al_dcgm_diag_pass.
set -u
out=/var/lib/node_exporter/textfile/dcgm_diag.prom
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
