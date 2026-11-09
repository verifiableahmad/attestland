#!/usr/bin/env bash
# Remove everything provision.sh created.
set -uo pipefail
WORK=${WORK:-/var/tmp/al-prov}
for d in $(virsh list --all --name | grep '^al-node-'); do
  virsh destroy "$d" 2>/dev/null; virsh undefine "$d" --remove-all-storage 2>/dev/null
done
virsh net-destroy al-prov 2>/dev/null; virsh net-undefine al-prov 2>/dev/null
docker rm -f al-sushy al-www 2>/dev/null
k3d cluster delete al-tink 2>/dev/null
pkill -f fleet_state_server.py 2>/dev/null
rm -rf "$WORK"
echo "provisioning lab removed"
