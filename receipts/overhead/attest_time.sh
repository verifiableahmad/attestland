#!/usr/bin/env bash
# Seconds to issue one receipt (hashing + CPU and GPU attestation + signing) for a fixed small job.
set -euo pipefail
[[ ! -r /etc/fleet/receipts.env ]] || { set -a; source /etc/fleet/receipts.env; set +a; }   # MAA_URL
platform_args=(--platform "${AL_PLATFORM:-azure}")
d=$(mktemp -d)
t0=$(date +%s.%N)
alissue --job-id "attest-time-$1" --model-dir /models/current --model-name al-gpt-124m \
  --inputs /opt/al/repo/receipts/overhead/fixed-requests.jsonl --outputs /opt/al/repo/receipts/overhead/fixed-requests.jsonl \
  --image "$VLLM_RECEIPTS_IMAGE" --bundle /var/lib/fleet/provenance.sigstore.json \
  "${platform_args[@]}" --out "$d" >/dev/null
echo "$(date +%s.%N) - $t0" | bc
rm -rf "$d"
