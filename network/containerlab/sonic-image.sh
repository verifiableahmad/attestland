#!/usr/bin/env bash
# Load docker-sonic-vs as $SONIC_VS_IMAGE (make lab-image). SONiC publishes it
# as a build artifact of the vs platform, not on a registry, and only for amd64.
# The build server serves the latest build of $SONIC_VS_BRANCH, so the pin is
# the artifact's sha256: set SONIC_VS_SHA256 in versions.env from the first
# download; a later build then fails the check instead of changing the lab.
set -euo pipefail
cd "$(dirname "$0")"
source ../../versions.env

if docker image inspect "$SONIC_VS_IMAGE" >/dev/null 2>&1; then
  echo "$SONIC_VS_IMAGE already loaded"; exit 0
fi
[[ $(uname -m) == x86_64 ]] || { echo "docker-sonic-vs is amd64 only; use LAB_NOS=frr here" >&2; exit 1; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
url="https://sonic-build.azurewebsites.net/api/sonic/artifacts?branchName=${SONIC_VS_BRANCH}&platform=vs&target=target/docker-sonic-vs.gz"
curl -fL --retry 3 -o "$tmp/docker-sonic-vs.gz" "$url"
sum=$(sha256sum "$tmp/docker-sonic-vs.gz" | cut -d' ' -f1)
if [[ -z ${SONIC_VS_SHA256:-} ]]; then
  echo "WARNING: SONIC_VS_SHA256 is not pinned; this build is sha256 $sum (pin it in versions.env)" >&2
elif [[ $sum != "$SONIC_VS_SHA256" ]]; then
  echo "docker-sonic-vs sha256 $sum != pinned $SONIC_VS_SHA256: the $SONIC_VS_BRANCH build moved; re-test and re-pin" >&2
  exit 1
fi
loaded=$(docker load -i "$tmp/docker-sonic-vs.gz" | sed -n 's/^Loaded image: //p' | tail -1)
docker tag "${loaded:-docker-sonic-vs:latest}" "$SONIC_VS_IMAGE"
echo "loaded $SONIC_VS_IMAGE (sha256 $sum)"
