#!/usr/bin/env bash
# Confidential VM bring-up (spec 7.2) on GCP a3-highgpu-1g: Intel TDX + 1x H100
# with NVIDIA CC on, Ubuntu 22.04 (ubuntu-os-cloud). Every step is checked.
# ASSUMPTIONS to confirm on the first real VM (listed in docs/external-deps.md):
#   - the platform turns GPU CC mode on; the guest only sets the ready state
#   - package names / flags for the CC-capable driver on the pinned branch
#   - `nvidia-smi conf-compute` flags (-f query, -grs get ready state, -srs set ready state)
#   - `go install .../cmd/gotpm@$GO_TPM_TOOLS_VERSION` resolves, and GCA issues
#     TDX tokens on a3-highgpu-1g (Google documents TDX attestation for C3)
set -euo pipefail
source /etc/fleet/versions.env
log() { printf '\n== %s\n' "$*"; }

log "0. Confirm this is a TDX guest with secure boot, a vTPM and configfs-tsm"
apt-get update
apt-get install -y linux-modules-extra-gcp mokutil
modprobe tdx_guest || true
grep -qw tdx_guest /proc/cpuinfo || dmesg | grep -qi "Intel TDX" || { echo "not a TDX guest"; exit 1; }
mokutil --sb-state | grep -q "SecureBoot enabled" || { echo "secure boot off"; exit 1; }
[[ -e /dev/tpmrm0 ]] || { echo "no vTPM"; exit 1; }
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
[[ -d /sys/kernel/config/tsm/report ]] || echo "WARN: no configfs-tsm; run alissue with --tdx-quote=false"

log "1. CC-capable NVIDIA driver (open kernel modules), persistence mode"
apt-get install -y build-essential
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

log "3. Attestation tools: NVIDIA NVAT (nvattest) and go-tpm-tools gotpm"
pip install "nv-attestation-sdk==${NVAT_VERSION}" 2>/dev/null || true   # name/channel: confirm (see external deps)
command -v nvattest >/dev/null || { echo "nvattest not installed: follow NVIDIA attestation install guide"; exit 1; }
if ! command -v gotpm >/dev/null; then
  curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | tar -C /usr/local -xz
  GOBIN=/usr/local/bin /usr/local/go/bin/go install "github.com/google/go-tpm-tools/cmd/gotpm@${GO_TPM_TOOLS_VERSION}"
fi
# The Confidential Computing API must be enabled in the project (OpenTofu does it).
nonce=$(head -c 32 /dev/urandom | xxd -p -c 64)
gotpm token --tee-technology tdx --custom-nonce "$nonce" --audience attestland-receipts \
  --output /var/lib/fleet/bringup-gca-token.jwt
python3 -c 'import base64,sys; p=open(sys.argv[1]).read().strip().split(".")[1]; print(base64.urlsafe_b64decode(p+"="*(-len(p)%4)).decode())' \
  /var/lib/fleet/bringup-gca-token.jwt > /var/lib/fleet/bringup-gca-claims.json
grep -q GCP_INTEL_TDX /var/lib/fleet/bringup-gca-claims.json || { echo "GCA token is not a TDX token"; exit 1; }

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
echo "bring-up complete; GCA claims in /var/lib/fleet/bringup-gca-claims.json (pin values in the policy)"
