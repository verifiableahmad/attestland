"""Drivers that let the same lab tests run against SONiC or FRR nodes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2] / "bench" / "lib"))

import fabric  # noqa: E402
import gen_sonic  # noqa: E402

LAB = "al-fabric"
_ANSI = re.compile(r"\x1b(\[[0-9;?]*[A-Za-z]|[78])")


def container(node: str) -> str:
    return f"clab-{LAB}-{node}"


def dexec(node: str, cmd: str, *, check: bool = True, timeout: int = 60) -> str:
    argv = ["docker", "exec", container(node), "sh", "-c", cmd]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{node}: {cmd!r} -> rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    return _ANSI.sub("", p.stdout).replace("\r", "")


class FRR:
    """Plain FRR containers: the kernel is the data plane, ports are the ethN wires."""
    name = "frr"

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

    def set_link(self, node: str, port: str, up: bool) -> None:
        dexec(node, f"ip link set {port} {'up' if up else 'down'}")

    def tx_bytes(self, node: str, port: str) -> int:
        return int(dexec(node, f"cat /sys/class/net/{port}/statistics/tx_bytes"))

    def wait_ready(self, fab) -> None:
        pass


class SONiC(FRR):
    """docker-sonic-vs. Routing state comes from its FRR, the same vtysh queries as
    FRR; link changes go through the SONiC CLI on the front-panel port, and byte
    counts come from the ethN veth behind it (the wire)."""
    name = "sonic"

    def l3_supported(self, node: str) -> bool:
        return True

    def set_link(self, node: str, port: str, up: bool) -> None:
        dexec(node, f"config interface {'startup' if up else 'shutdown'} {gen_sonic.sonic_port(port)}")

    def wait_ready(self, fab, timeout: float = 300) -> None:
        """setup.sh runs at deploy and touches READY last; wait for every switch."""
        deadline = time.time() + timeout
        for n in fab.spines + fab.leaves:
            while dexec(n.name, f"test -e {gen_sonic.READY} && echo ok", check=False).strip() != "ok":
                if time.time() > deadline:
                    pytest.fail(f"{n.name}: setup.sh did not finish (docker logs, /tmp on the node)")
                time.sleep(2)


@pytest.fixture(scope="session")
def nos(fab):
    which = os.environ.get("LAB_NOS", "sonic")
    drivers = {"sonic": SONiC, "frr": FRR}
    if which not in drivers:
        pytest.fail(f"LAB_NOS={which}: expected one of {sorted(drivers)}")
    d = drivers[which]()
    if _lab_up():               # session fixtures run before lab_running's skip
        d.wait_ready(fab)
    return d


@pytest.fixture(scope="session")
def fab():
    return fabric.build()


def _lab_up() -> bool:
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True).stdout.split()
    return container("leaf1") in names


@pytest.fixture(autouse=True)
def lab_running(request):
    if request.node.get_closest_marker("offline"):
        return
    if not _lab_up():
        pytest.skip("lab not running: run `make lab-up` first")
