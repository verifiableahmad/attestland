"""Drivers that let the same lab tests run against SR Linux or FRR nodes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2] / "bench" / "lib"))

import fabric  # noqa: E402

LAB = "al-fabric"
_ANSI = re.compile(r"\x1b(\[[0-9;?]*[A-Za-z]|[78])")


def container(node: str) -> str:
    return f"clab-{LAB}-{node}"


def dexec(node: str, cmd: str, *, check: bool = True, tty: bool = False,
          timeout: int = 60) -> str:
    argv = ["docker", "exec"] + (["-t"] if tty else []) + [container(node), "sh", "-c", cmd]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{node}: {cmd!r} -> rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    return _ANSI.sub("", p.stdout).replace("\r", "")


class FRR:
    name = "frr"

    @staticmethod
    def port(srl_port: str) -> str:
        return "eth" + srl_port.split("/")[1]

    def bgp_states(self, node: str) -> dict[str, str]:
        s = json.loads(dexec(node, "vtysh -c 'show bgp summary json'"))
        out = {}
        for afi, body in s.items():
            for peer, p in body.get("peers", {}).items():
                out[f"{afi}:{peer}"] = p.get("state", "?")
        return out

    def evpn_route_count(self, node: str, rtype: int) -> int:
        kind = {2: "macip", 5: "prefix"}[rtype]
        s = json.loads(dexec(node, f"vtysh -c 'show bgp l2vpn evpn route type {kind} json'"))
        return int(s.get("numPrefix", 0))

    def l3_supported(self, node: str) -> bool:
        return dexec(node, "cat /tmp/vrf-support", check=False).strip() == "vrf-support=1"

    def set_link(self, node: str, srl_port: str, up: bool) -> None:
        dexec(node, f"ip link set {self.port(srl_port)} {'up' if up else 'down'}")

    def tx_bytes(self, node: str, srl_port: str) -> int:
        return int(dexec(node, f"cat /sys/class/net/{self.port(srl_port)}/statistics/tx_bytes"))


class SRL:
    """SR Linux driver. sr_cli needs a TTY under docker exec."""
    name = "srl"

    def _cli(self, node: str, cmd: str) -> str:
        return dexec(node, f"sr_cli -d \"{cmd}\"", tty=True)

    def _json(self, node: str, cmd: str):
        out = self._cli(node, f"{cmd} | as json")
        return json.loads(out[out.index("{"):])

    def bgp_states(self, node: str) -> dict[str, str]:
        s = self._json(node, "info from state network-instance default protocols bgp neighbor *")
        nbrs = (s.get("network-instance", [{}])[0].get("protocols", {})
                .get("bgp", {}).get("neighbor", []))
        return {n["peer-address"]: n.get("session-state", "?") for n in nbrs}

    def evpn_route_count(self, node: str, rtype: int) -> int:
        out = self._cli(node, f"show network-instance default protocols bgp routes evpn route-type {rtype} summary")
        m = re.search(r"(\d+)\s+(?:received|Type \d routes)", out)
        if m:
            return int(m.group(1))
        return sum(1 for line in out.splitlines() if re.match(r"^\|\s*[u*>]", line))

    def l3_supported(self, node: str) -> bool:
        return True

    def set_link(self, node: str, srl_port: str, up: bool) -> None:
        state = "enable" if up else "disable"
        dexec(node, f"sr_cli -ec 'interface {srl_port} admin-state {state}' && "
                    f"sr_cli -ec 'commit now'", tty=True)

    def tx_bytes(self, node: str, srl_port: str) -> int:
        s = self._json(node, f"info from state interface {srl_port} statistics out-octets")
        return int(s["interface"][0]["statistics"]["out-octets"])


@pytest.fixture(scope="session")
def nos():
    which = os.environ.get("LAB_NOS", "srl")
    return FRR() if which == "frr" else SRL()


@pytest.fixture(scope="session")
def fab():
    return fabric.build()


@pytest.fixture(autouse=True)
def lab_running(request):
    if request.node.get_closest_marker("offline"):
        return
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True).stdout.split()
    if container("leaf1") not in names:
        pytest.skip("lab not running: run `make lab-up` first")
