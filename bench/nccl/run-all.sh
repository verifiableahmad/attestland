#!/usr/bin/env bash
# make nccl: every row of spec 2.2 x 4 collectives x 3 repeats, then parse into result files.
# make nccl-smoke (SMOKE=1): one short 2-node all_reduce over TCP that proves both
# ranks join and the traffic goes over NET/Socket; writes no result file.
# Exits non-zero if any job, transport check or rank check failed.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPEATS=${REPEATS:-3}
NODES=${NODES:-2}
PARTITION=${PARTITION:-gpu}
# A job that has not started PEND_MIN minutes after submission is cancelled
# (state DEADLINE) instead of pending forever. 30 = nccl.sbatch's time limit.
PEND_MIN=${PEND_MIN:-15}
deadline="now+$(( PEND_MIN + 30 ))minutes"

avail=$(sinfo -h -p "$PARTITION" -t idle -o %D | awk '{s += $1} END {print s + 0}')
if (( avail < NODES )); then
  echo "need $NODES idle Slurm nodes in partition $PARTITION, have $avail:" >&2
  sinfo -N -p "$PARTITION" -o '%N %T %E' >&2
  echo "a node in the k8s pool shows drained, reason pool=k8s; move it: sudo /opt/al/bin/pool-move <node> slurm" >&2
  exit 1
fi
# GPUs per node (gpu:1, or gpu:<type>:1(S:0) with a typed GRES), for the intra runs.
gpus=$(sinfo -h -p "$PARTITION" -o %G | head -1 | sed -E 's/.*gpu:([^:( ]*:)?([0-9]+).*/\2/')
[[ $gpus =~ ^[0-9]+$ ]] || gpus=1

fail=0
run() {  # run <collective> <transport> <nodes> <gpus per node> <repeat> [parse_nccl.py options]
  local coll=$1 transport=$2 n=$3 g=$4 rep=$5 jid; shift 5
  echo "$(date -u +%H:%M:%S) $coll/$transport rep $rep on $n node(s)"
  # --wait: sbatch exits with the job's exit code once it ends.
  if ! jid=$(sbatch --parsable --wait --deadline="$deadline" -N "$n" --gpus-per-node="$g" \
      bench/nccl/nccl.sbatch "$coll" "$transport"); then
    echo "FAILED $coll/$transport rep $rep: job ${jid:-?} $(sacct -nX -j "${jid:-0}" -o State,ExitCode 2>/dev/null | xargs)" >&2
    fail=1
    return
  fi
  .venv/bin/python bench/nccl/parse_nccl.py "/scratch/logs/nccl-$jid.log" --collective "$coll" \
    --transport "$transport" --nodes "$n" --repeat "$rep" "$@" || fail=1
}

if [[ ${SMOKE:-0} == 1 ]]; then
  export MAX_BYTES=64M   # sbatch passes the environment to the job
  run all_reduce_perf tcp "$NODES" 1 0 --check-only
  exit $fail
fi

for rep in $(seq 1 "$REPEATS"); do
  for coll in all_reduce_perf all_gather_perf reduce_scatter_perf sendrecv_perf; do
    for transport in intra tcp rdma; do
      if [[ $transport == intra ]]; then
        # One rank measures nothing: bus bandwidth is 0 by definition (NCCL-7).
        (( gpus >= 2 )) || { echo "skip $coll/intra: $gpus GPU per node"; continue; }
        run "$coll" intra 1 "$gpus" "$rep"
      else
        run "$coll" "$transport" "$NODES" 1 "$rep"
      fi
    done
  done
done
(( fail == 0 )) || echo "make nccl: some runs failed; see the FAILED lines above" >&2
exit $fail
