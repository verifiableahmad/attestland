#!/usr/bin/env bash
# Seconds from container start to vLLM /health OK (model load + engine start).
set -euo pipefail
docker rm -f al-load >/dev/null 2>&1 || true
t0=$(date +%s.%N)
docker run -d --name al-load --gpus all --ipc host -p 8001:8000 -v "$1:/models/current:ro" "$VLLM_RECEIPTS_IMAGE" \
  --model /models/current >/dev/null
until curl -sf localhost:8001/health >/dev/null; do sleep 0.5; done
echo "$(date +%s.%N) - $t0" | bc
docker rm -f al-load >/dev/null
