#!/usr/bin/env bash
# make provision N=3: blank libvirt VMs -> Burn-in with no manual step.
#
# Needs a Linux host with KVM, libvirt, virt-install, qemu-img, docker, k3d,
# helm and kubectl. Records the wall time in bench/results/fleet/provision-*.json.
set -euo pipefail
N=${1:-3}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
source "$REPO/versions.env"
WORK=${WORK:-/var/tmp/al-prov}
HOST_IP=10.42.0.1
BMC_USER=admin
BMC_PASS=${BMC_PASS:-$(openssl rand -hex 12)}
mkdir -p "$WORK"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
for t in virsh virt-install qemu-img docker k3d helm kubectl htpasswd python3 jq; do need "$t"; done
[[ -e /dev/kvm ]] || { echo "no /dev/kvm: provisioning simulation needs a KVM host" >&2; exit 1; }

START=$(date +%s)

# 1. Provisioning network and blank VMs (defined, powered off, network boot first).
virsh net-info al-prov >/dev/null 2>&1 || { virsh net-define "$HERE/libvirt/network.xml"; virsh net-start al-prov; }
for i in $(seq 1 "$N"); do
  name=al-node-$i
  mac=$(printf '52:54:00:ac:00:%02x' "$i")
  if ! virsh dominfo "$name" >/dev/null 2>&1; then
    log "define $name ($mac)"
    virt-install --name "$name" --memory 4096 --vcpus 2 \
      --disk "path=$WORK/$name.qcow2,size=20,format=qcow2" \
      --network "network=al-prov,mac=$mac,model=virtio" \
      --boot network,hd --pxe --os-variant ubuntu22.04 \
      --graphics none --noautoconsole --print-xml > "$WORK/$name.xml"
    virsh define "$WORK/$name.xml" >/dev/null
  fi
done

# 2. Redfish BMC emulator (sushy-tools) in front of libvirt.
mkdir -p "$WORK/sushy"
htpasswd -bBc "$WORK/sushy/htpasswd" "$BMC_USER" "$BMC_PASS" 2>/dev/null
cp "$HERE/libvirt/sushy.conf" "$WORK/sushy/sushy.conf"
docker rm -f al-sushy >/dev/null 2>&1 || true
docker run -d --name al-sushy --network host \
  -v /var/run/libvirt:/var/run/libvirt -v "$WORK/sushy:/etc/sushy" \
  "quay.io/metal3-io/sushy-tools:${SUSHY_TOOLS_VERSION}" \
  sushy-emulator --config /etc/sushy/sushy.conf >/dev/null
until curl -sfu "$BMC_USER:$BMC_PASS" "http://$HOST_IP:8000/redfish/v1/Systems" >/dev/null; do sleep 1; done
log "redfish up at http://$HOST_IP:8000"

# 3. OS image served over HTTP: Ubuntu 22.04 cloud image converted to raw.gz.
mkdir -p "$WORK/www"
if [[ ! -f "$WORK/www/jammy.raw.gz" ]]; then
  curl -fL -o "$WORK/jammy.img" https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img
  qemu-img convert -O raw "$WORK/jammy.img" "$WORK/jammy.raw"
  gzip -1 -c "$WORK/jammy.raw" > "$WORK/www/jammy.raw.gz"
fi
cp "$HERE/agent/node-agent.sh" "$HERE/agent/node-agent.service" "$WORK/www/"
docker rm -f al-www >/dev/null 2>&1 || true
docker run -d --name al-www --network host -v "$WORK/www:/usr/share/nginx/html:ro" \
  -e NGINX_PORT=8080 nginx:1.27-alpine sh -c \
  "sed -i 's/listen  *80;/listen 8080;/' /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'" >/dev/null

# 4. Fleet state endpoint: nodes report their lifecycle state here.
pkill -f fleet_state_server.py 2>/dev/null || true
python3 "$HERE/agent/fleet_state_server.py" --listen "$HOST_IP:8090" --db "$WORK/states.json" &
STATE_PID=$!
trap 'kill $STATE_PID 2>/dev/null || true' EXIT

# 5. Tinkerbell stack (Smee DHCP/iPXE, Tink, Hegel, Rufio) on a k3d cluster.
k3d cluster list al-tink >/dev/null 2>&1 || \
  k3d cluster create al-tink --network host --no-lb \
    --k3s-arg "--disable=traefik@server:0" --k3s-arg "--disable=servicelb@server:0"
helm upgrade --install tink-stack oci://ghcr.io/tinkerbell/charts/stack \
  --version "${TINKERBELL_STACK_VERSION#v}" --namespace tink-system --create-namespace \
  -f "$HERE/tinkerbell/stack-values.yaml" --set "global.publicIP=$HOST_IP" --wait

kubectl -n tink-system create secret generic bmc-creds \
  --from-literal=username="$BMC_USER" --from-literal=password="$BMC_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$HERE/tinkerbell/template.yaml"

# 6. Register each VM: Hardware (MAC, IP, disk) + Machine (Redfish BMC) + Workflow.
for i in $(seq 1 "$N"); do
  name=al-node-$i
  uuid=$(virsh domuuid "$name")
  mac=$(printf '52:54:00:ac:00:%02x' "$i")
  ip="10.42.0.$((10 + i))"
  sed -e "s/__NAME__/$name/g" -e "s/__MAC__/$mac/g" -e "s/__IP__/$ip/g" \
      -e "s/__UUID__/$uuid/g" -e "s/__HOST_IP__/$HOST_IP/g" \
      "$HERE/tinkerbell/node.yaml.tmpl" | kubectl apply -f -
done

# 7. Wait until every node has reported Burn-in.
log "waiting for $N nodes to reach Burn-in"
deadline=$(( $(date +%s) + 3600 ))
while :; do
  n_ready=$(jq '[.[] | select(.state=="Burn-in")] | length' "$WORK/states.json" 2>/dev/null || echo 0)
  (( n_ready >= N )) && break
  (( $(date +%s) > deadline )) && { echo "timeout: $n_ready/$N in Burn-in" >&2; exit 1; }
  sleep 5
done
END=$(date +%s)
log "all $N nodes in Burn-in after $((END - START)) s"

"$REPO/.venv/bin/python" - "$N" "$((END - START))" "$WORK/states.json" "$REPO" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[4] + "/bench/lib")
from result import write_result
n, secs, db = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
states = json.load(open(db))
p = write_result("fleet", "provision",
    config={"nodes": n, "method": "tinkerbell+sushy-tools", "image": "ubuntu-22.04"},
    metrics={"wall_time_s": secs, "per_node_s": {k: v.get("elapsed_s") for k, v in states.items()}},
    hardware={"instance_type": "libvirt-vm-2vcpu-4g", "region": "local", "cloud": "local"})
print(p)
PY
