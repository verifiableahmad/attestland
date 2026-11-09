#!/usr/bin/env bash
# Integration test on a GPU node (spec 1.3): inject an XID through DCGM so the
# DCGM exporter -> Prometheus -> healthd path sees it, without a real fault.
# Field 230 = DCGM_FI_DEV_XID_ERRORS. Check the flags against the installed
# DCGM release: `dcgmi test --help` (syntax has changed across versions).
#   inject-xid.sh <gpu-id> <xid>
set -euo pipefail
gpu=${1:-0} xid=${2:-79}
date -u +%s.%N > /run/al-last-injection
dcgmi test --inject --gpuid "$gpu" -f 230 -v "$xid"
echo "injected XID $xid on GPU $gpu at $(cat /run/al-last-injection)"
