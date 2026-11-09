#!/usr/bin/env bash
# make nccl: every row of spec 2.2 x 4 collectives x 3 repeats, then parse into result files.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPEATS=${REPEATS:-3}
NODES=${NODES:-2}
for rep in $(seq 1 "$REPEATS"); do
  for coll in all_reduce_perf all_gather_perf reduce_scatter_perf sendrecv_perf; do
    for transport in intra tcp rdma; do
      n=$NODES; [[ $transport == intra ]] && n=1
      jid=$(sbatch --parsable -N "$n" bench/nccl/nccl.sbatch "$coll" "$transport")
      echo "submitted $coll/$transport rep $rep as $jid"
      while squeue -h -j "$jid" | grep -q .; do sleep 5; done
      log=/scratch/logs/nccl-$jid.log
      [[ -f $log ]] || log=$(ls -t /scratch/logs/nccl-*.log | head -1)
      .venv/bin/python bench/nccl/parse_nccl.py "$log" --collective "$coll" \
        --transport "$transport" --nodes "$n" --repeat "$rep" || true
    done
  done
done
