#!/bin/sh
# Node agent, first-boot part: report lifecycle state to the fleet endpoint.
# The burn-in pipeline (fleet/burnin) takes over once the node is in Burn-in.
set -eu
. /etc/fleet/agent.env
STATE_FILE=/var/lib/fleet/state
mkdir -p /var/lib/fleet

report() {
  echo "$1" > "$STATE_FILE"
  body=$(printf '{"node":"%s","state":"%s","boot_id":"%s","uptime_s":%s}' \
    "$(hostname)" "$1" "$(cat /proc/sys/kernel/random/boot_id)" "$(cut -d. -f1 /proc/uptime)")
  i=0
  until curl -fsS -m 5 -H 'Content-Type: application/json' -d "$body" "$FLEET_STATE_URL/state"; do
    i=$((i + 1)); [ "$i" -ge 60 ] && return 1; sleep 5
  done
}

current=$(cat "$STATE_FILE" 2>/dev/null || echo Provisioning)
case "$current" in
  Provisioning) report "${FLEET_HANDOFF:-Burn-in}" ;;
  *) report "$current" ;;   # re-announce after reboot
esac
