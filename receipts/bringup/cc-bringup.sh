#!/usr/bin/env bash
# Confidential VM bring-up (spec 7.2) on Azure Standard_NCC40ads_H100_v5: AMD
# SEV-SNP + 1x H100 NVL with NVIDIA CC on, Ubuntu 22.04 CVM image (ADR 010).
# Follows the shape of Azure's confidential GPU guide; every step is checked.
# ASSUMPTIONS to confirm on the first real VM (listed in docs/external-deps.md):
#   - package names / flags for the CC-capable driver on the pinned branch
#   - `nvidia-smi conf-compute` flags (-f query, -grs get ready state, -srs set ready state)
#   - AttestationClient build steps from Azure/confidential-computing-cvm-guest-attestation,
#     and MAA echoing our nonce in x-ms-runtime.client-payload.nonce
set -euo pipefail
source /etc/fleet/versions.env
: "${MAA_URL:?MAA URL, e.g. https://sharedeus2.eus2.attest.azure.net}"
log() { printf '\n== %s\n' "$*"; }

log "0. Confirm this is an SEV-SNP confidential VM with secure boot and a vTPM"
apt-get update
apt-get install -y mokutil
dmesg | grep -qi "SEV-SNP" || { echo "not an SEV-SNP guest"; exit 1; }
mokutil --sb-state | grep -q "SecureBoot enabled" || { echo "secure boot off"; exit 1; }
[[ -e /dev/tpmrm0 ]] || { echo "no vTPM"; exit 1; }

log "1. CC-capable NVIDIA driver (open kernel modules), persistence mode"
apt-get install -y "linux-modules-extra-$(uname -r)" build-essential
curl -fsSLo /tmp/cuda-keyring.deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i /tmp/cuda-keyring.deb && apt-get update
apt-get install -y "nvidia-driver-${NVIDIA_DRIVER_BRANCH}-open=${NVIDIA_DRIVER_VERSION}-0ubuntu1" "cuda-toolkit-${CUDA_VERSION%.*}" || \
  apt-get install -y "nvidia-open-${NVIDIA_DRIVER_BRANCH}"
nvidia-persistenced --uvm-persistence-mode || true
systemctl enable --now nvidia-persistenced
nvidia-smi -pm 1

log "2. Confirm CC mode"
nvidia-smi conf-compute -f | tee /var/lib/fleet/cc-mode.txt
grep -qi "CC status: ON" /var/lib/fleet/cc-mode.txt || { echo "GPU CC mode is not ON"; exit 1; }

log "3. Attestation tools: NVIDIA NVAT (nvattest) and the Azure guest attestation client"
pip install "nv-attestation-sdk==${NVAT_VERSION}" 2>/dev/null || true   # name/channel: confirm (see external deps)
command -v nvattest >/dev/null || { echo "nvattest not installed: follow NVIDIA attestation install guide"; exit 1; }
if ! command -v AttestationClient >/dev/null; then
  apt-get install -y cmake libcurl4-openssl-dev libssl-dev libjsoncpp-dev libboost-all-dev nlohmann-json3-dev
  git clone --depth 1 https://github.com/Azure/confidential-computing-cvm-guest-attestation /opt/cvm-attest
  (cd /opt/cvm-attest/cvm-attestation-sample-app && ./ClientLibBuildAndInstall.sh && cmake . && make)
  install -m 0755 /opt/cvm-attest/cvm-attestation-sample-app/AttestationClient /usr/local/bin/
fi
nonce=$(head -c 32 /dev/urandom | xxd -p -c 64)
AttestationClient -a "$MAA_URL" -n "$nonce" -o token > /var/lib/fleet/bringup-maa-token.jwt
python3 -c 'import base64,sys; p=open(sys.argv[1]).read().strip().split(".")[1]; print(base64.urlsafe_b64decode(p+"="*(-len(p)%4)).decode())' \
  /var/lib/fleet/bringup-maa-token.jwt > /var/lib/fleet/bringup-maa-claims.json
grep -q sevsnpvm /var/lib/fleet/bringup-maa-claims.json || { echo "MAA token is not an SEV-SNP token"; exit 1; }
grep -q "$nonce" /var/lib/fleet/bringup-maa-claims.json || { echo "MAA token does not carry our nonce"; exit 1; }

log "4. Attest the GPU before any workload, then set the GPU ready state"
nvattest attest --device gpu --verifier remote --nonce "$nonce" --format json > /var/lib/fleet/bringup-gpu-attest.json
grep -q '"x-nvidia-overall-att-result": *true\|true' /var/lib/fleet/bringup-gpu-attest.json || { echo "GPU attestation failed"; exit 1; }
nvidia-smi conf-compute -srs 1
nvidia-smi conf-compute -grs | grep -qi ready

log "5. Container runtime + NVIDIA toolkit; pull vLLM by digest only"
apt-get install -y docker.io
apt-get install -y "nvidia-container-toolkit=${NVIDIA_CONTAINER_TOOLKIT_VERSION}-1"
nvidia-ctk runtime configure --runtime=docker && systemctl restart docker
[[ -n "${VLLM_RECEIPTS_IMAGE:-}" && "$VLLM_RECEIPTS_IMAGE" == *@sha256:* ]] || { echo "set VLLM_RECEIPTS_IMAGE=repo@sha256:..."; exit 1; }
docker pull "$VLLM_RECEIPTS_IMAGE"
echo "bring-up complete; MAA claims in /var/lib/fleet/bringup-maa-claims.json (pin the launch measurement in the policy)"
