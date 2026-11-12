#!/usr/bin/env bash
# One receipted inference job in the confidential VM (spec 7.3-7.5):
#   1. verify the image signature for the expected workflow identity (cosign verify)
#   2. serve the model with vLLM from the image, by digest
#   3. send the job's requests, record responses (canonical JSONL)
#   4. alissue: hash, fresh key, nonce, CPU + GPU attestation, sign
#   run-job.sh <job-id> <requests.jsonl> <model-dir> <out-dir>
set -euo pipefail
job=${1:?job id} reqs=${2:?requests.jsonl} model=${3:?model dir} out=${4:?out dir}
: "${VLLM_RECEIPTS_IMAGE:?repo@sha256:...}" "${CERT_IDENTITY:?workflow identity}"
# AL_PLATFORM: azure (default; MAA_URL from /etc/fleet/receipts.env) | test
[[ ! -r /etc/fleet/receipts.env ]] || { set -a; source /etc/fleet/receipts.env; set +a; }
platform_args=(--platform "${AL_PLATFORM:-azure}")
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"

cosign verify "$VLLM_RECEIPTS_IMAGE" --certificate-identity "$CERT_IDENTITY" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com > /dev/null
cosign download attestation --predicate-type https://slsa.dev/provenance/v1 --bundle \
  "$VLLM_RECEIPTS_IMAGE" > "$out/provenance.sigstore.json" 2>/dev/null || \
  gh attestation download "oci://$VLLM_RECEIPTS_IMAGE" -R "${GH_REPO:?owner/repo}" -o "$out"

started=$(date -u +%FT%TZ)
docker rm -f al-vllm 2>/dev/null || true
docker run -d --name al-vllm --gpus all --ipc host -p 8000:8000 -v "$model:/models/current:ro" \
  "$VLLM_RECEIPTS_IMAGE" --model /models/current --served-model-name al-gpt
t0=$(date +%s.%N)
until curl -sf localhost:8000/health >/dev/null; do sleep 1; done
echo "model_load_s=$(echo "$(date +%s.%N) - $t0" | bc)" > "$out/timings.env"

python3 "$HERE/client.py" --url http://localhost:8000 --model al-gpt --requests "$reqs" --responses "$out/responses.raw.jsonl"
finished=$(date -u +%FT%TZ)
docker rm -f al-vllm >/dev/null

t1=$(date +%s.%N)
alissue --job-id "$job" --started-at "$started" --finished-at "$finished" \
  --model-dir "$model" --model-name al-gpt-124m --inputs "$reqs" --outputs "$out/responses.raw.jsonl" \
  --image "$VLLM_RECEIPTS_IMAGE" --bundle "$out"/*.json* "${platform_args[@]}" --out "$out"
echo "receipt_s=$(echo "$(date +%s.%N) - $t1" | bc)" >> "$out/timings.env"
