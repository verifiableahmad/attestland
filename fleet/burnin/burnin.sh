#!/usr/bin/env bash
# Burn-in for one GPU node (spec 1.2). Runs every check, writes raw outputs and a
# JSON report; exit 0 only if every check passes. Target: under 30 minutes.
#
#   burnin.sh --peer <known-good peer IP> --sku g2-standard-8 --report /var/lib/fleet/burnin-report.json
#
# The inter-node checks need root SSH to the peer (ansible sets up the burn-in key).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PEER="" SKU="" REPORT=/var/lib/fleet/burnin-report.json MTU=${MTU:-8896}
STRESS_S=${STRESS_S:-600} FLEET_MEDIAN=${FLEET_MEDIAN:-}
while [[ $# -gt 0 ]]; do
  case $1 in
    --peer) PEER=$2; shift ;;
    --sku) SKU=$2; shift ;;
    --report) REPORT=$2; shift ;;
    --fleet-median-busbw) FLEET_MEDIAN=$2; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
  shift
done
[[ -n $PEER && -n $SKU ]] || { echo "need --peer and --sku" >&2; exit 2; }
RAW=$(mktemp -d /var/lib/fleet/burnin-raw.XXXX)
NODE=$(hostname -s)
NGPUS=$(nvidia-smi -L | wc -l)
IFACE=$(ip -o -4 route show to default | awk '{print $5}' | head -1)
log() { printf '%s burnin %s: %s\n' "$(date -u +%H:%M:%S)" "$NODE" "$*"; }
t0=$(date +%s)

log "1/8 dcgmi diag -r 3"
dcgmi diag -r 3 -j > "$RAW/dcgm_diag_r3.json" 2>&1

log "2/8 ECC and row remapping"
nvidia-smi -q -d ECC,ROW_REMAPPER > "$RAW/nvidia_smi_ecc.txt" 2>&1

log "3/8 sustained load ${STRESS_S}s with throttle sampling"
dcgmi diag -r targeted_stress -p "targeted_stress.test_duration=${STRESS_S}" -j > "$RAW/stress_diag.json" 2>&1 &
stress=$!
echo "timestamp,hw_slowdown,hw_thermal_slowdown,sw_thermal_slowdown,hw_power_brake_slowdown" > "$RAW/throttle_samples.csv"
while kill -0 "$stress" 2>/dev/null; do
  nvidia-smi --query-gpu=timestamp,clocks_event_reasons.hw_slowdown,clocks_event_reasons.hw_thermal_slowdown,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_power_brake_slowdown \
    --format=csv,noheader >> "$RAW/throttle_samples.csv"
  sleep 5
done
wait "$stress"

log "4/8 intra-node all_reduce"
/opt/nccl-tests/build/all_reduce_perf -b 1G -e 1G -g "$NGPUS" -w 5 -n 20 > "$RAW/nccl_intra.txt" 2>&1

log "5/8 inter-node all_reduce with $PEER"
NCCL_IB_DISABLE=1 NCCL_SOCKET_IFNAME=$IFACE mpirun --allow-run-as-root -np 2 -H "localhost:1,$PEER:1" \
  -x NCCL_IB_DISABLE -x NCCL_SOCKET_IFNAME -x LD_LIBRARY_PATH --mca btl_tcp_if_include "$IFACE" \
  /opt/nccl-tests/build/all_reduce_perf -b 256M -e 256M -g 1 -w 5 -n 20 > "$RAW/nccl_inter.txt" 2>&1

log "6/8 fio on /scratch"
for spec in "seqread read 1M" "seqwrite write 1M" "randread randread 4k" "randwrite randwrite 4k"; do
  set -- $spec
  fio --name="$1" --filename=/scratch/.burnin-fio --size=8G --rw="$2" --bs="$3" --direct=1 \
      --ioengine=libaio --iodepth=64 --numjobs=4 --group_reporting --time_based --runtime=30 \
      --output-format=json > "$RAW/fio_$1.json" 2>/dev/null
done
rm -f /scratch/.burnin-fio

log "7/8 iperf3 and path MTU to $PEER"
ssh -o StrictHostKeyChecking=no "root@$PEER" 'pkill iperf3; iperf3 -s -D -1' >/dev/null 2>&1
sleep 1
iperf3 -c "$PEER" -P 8 -t 15 -J > "$RAW/iperf3.json" 2>&1
if ping -c 3 -M do -s $((MTU - 28)) "$PEER" >/dev/null 2>&1; then echo MTU_OK > "$RAW/mtu_check.txt"; else echo MTU_FAIL > "$RAW/mtu_check.txt"; fi

log "8/8 software versions"
nccl=$(dpkg-query -W -f='${Version}' libnccl2 2>/dev/null)
cuda=$(cat /usr/local/cuda/version.json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["cuda"]["version"])' 2>/dev/null)
ctk=$(nvidia-ctk --version 2>/dev/null | grep -o '[0-9]*\.[0-9]*\.[0-9]*' | head -1)
drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
printf '{"driver":"%s","cuda":"%s","nccl":"%s","container_toolkit":"%s"}\n' "$drv" "$cuda" "$nccl" "$ctk" > "$RAW/software.json"

python3 "$HERE/evaluate.py" --raw "$RAW" --sku "$SKU" --node "$NODE" --ngpus "$NGPUS" --mtu "$MTU" \
  ${FLEET_MEDIAN:+--fleet-median-busbw "$FLEET_MEDIAN"} --report "$REPORT" \
  --textfile /var/lib/node_exporter/textfile/burnin.prom
rc=$?
log "done in $(( $(date +%s) - t0 )) s (rc=$rc), raw outputs in $RAW"
exit $rc
