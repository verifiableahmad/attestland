#!/usr/bin/env bash
# Run containerlab natively on Linux, or inside its own container elsewhere
# (macOS / Docker Desktop). Arguments pass straight through.
set -euo pipefail
cd "$(dirname "$0")"
source ../../versions.env
if command -v containerlab >/dev/null 2>&1; then
  exec sudo containerlab "$@"
fi
exec docker run --rm -i --privileged --network host --pid host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/run/netns:/var/run/netns \
  -v /var/lib/docker/containers:/var/lib/docker/containers \
  -v "$PWD:$PWD" -w "$PWD" \
  "ghcr.io/srl-labs/clab:${CONTAINERLAB_VERSION}" containerlab "$@"
