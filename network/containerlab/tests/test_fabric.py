"""make lab-test: control plane, reachability, isolation, ECMP, convergence."""

from __future__ import annotations

import re
import subprocess
import time

import pytest

import fabric
from conftest import container, dexec

UPLINKS = tuple(f"eth{fabric.UPLINK_PORT_BASE + s}" for s in range(fabric.N_SPINES))


def _wait(pred, timeout=60, step=2):
    deadline = time.time() + timeout
    while True:
        ok = pred()
        if ok or time.time() > deadline:
            return ok
        time.sleep(step)


def test_all_bgp_sessions_established(nos, fab):
    def all_up():
        bad = {}
        for n in fab.spines + fab.leaves:
            for peer, state in nos.bgp_states(n.name).items():
                if state.lower() != "established":
                    bad[f"{n.name}/{peer}"] = state
        return bad
    _wait(lambda: not all_up(), timeout=90)
    bad = all_up()
    assert not bad, f"sessions not established: {bad}"
    # every leaf has one underlay (and one overlay) session per spine
    for leaf in fab.leaves:
        assert len(nos.bgp_states(leaf.name)) >= len(fab.spines)


def test_evpn_type2_on_every_leaf(nos, fab):
    for leaf in fab.leaves:
        assert _wait(lambda: nos.evpn_route_count(leaf.name, 2) > 0, timeout=60), \
            f"{leaf.name}: no EVPN type-2 routes"


def test_evpn_type5_on_every_leaf(nos, fab):
    if not nos.l3_supported(fab.leaves[0].name):
        pytest.skip("kernel lacks VRF support (Docker Desktop LinuxKit): "
                    "L3 VNIs and type-5 routes need a Linux host")
    for leaf in fab.leaves:
        assert _wait(lambda: nos.evpn_route_count(leaf.name, 5) > 0, timeout=60), \
            f"{leaf.name}: no EVPN type-5 routes"


def _ping(src: str, dst_ip: str, count: int = 3) -> bool:
    out = subprocess.run(["docker", "exec", container(src), "ping", "-c", str(count),
                          "-W", "1", "-i", "0.2", dst_ip],
                         capture_output=True, text=True)
    return out.returncode == 0


def test_same_vni_reaches_across_leaves(fab):
    for tenant in ("red", "blue"):
        hosts = [h for h in fab.hosts if h.tenant.name == tenant]
        src = hosts[0]
        for dst in hosts[1:]:
            ip = dst.address.split("/")[0]
            assert _wait(lambda: _ping(src.name, ip), timeout=30, step=1), \
                f"{src.name} ({tenant}) cannot reach {dst.name} {ip} on {dst.leaf}"


def test_tenants_are_isolated(fab):
    red = [h for h in fab.hosts if h.tenant.name == "red"]
    blue = [h for h in fab.hosts if h.tenant.name == "blue"]
    for dst in blue:
        ip = dst.address.split("/")[0]
        assert not _ping(red[0].name, ip, count=2), f"red {red[0].name} reached blue {ip}"


def test_ecmp_spreads_flows_across_spines(nos, fab):
    src = next(h for h in fab.hosts if h.leaf == "leaf1" and h.tenant.name == "red")
    dst = next(h for h in fab.hosts if h.leaf == "leaf2" and h.tenant.name == "red")
    dst_ip = dst.address.split("/")[0]
    dexec(dst.name, "pkill iperf3; iperf3 -s -D", check=False)
    time.sleep(1)
    before = [nos.tx_bytes("leaf1", p) for p in UPLINKS]
    dexec(src.name, f"iperf3 -c {dst_ip} -P 16 -t 3 -b 20M", timeout=30)
    after = [nos.tx_bytes("leaf1", p) for p in UPLINKS]
    delta = [a - b for a, b in zip(after, before)]
    total = sum(delta)
    assert total > 1_000_000, f"too little traffic on uplinks: {delta}"
    shares = [d / total for d in delta]
    assert min(shares) > 0.10, f"flows did not spread across spines: {delta}"


def test_spine_link_failure_convergence(nos, fab):
    """Shut the uplink carrying a continuous ping; record loss and convergence."""
    from result import write_result

    src = next(h for h in fab.hosts if h.leaf == "leaf1" and h.tenant.name == "red")
    dst = next(h for h in fab.hosts if h.leaf == "leaf3" and h.tenant.name == "red")
    dst_ip = dst.address.split("/")[0]
    interval = 0.01
    # find which uplink this ICMP flow hashes onto
    before = [nos.tx_bytes("leaf1", p) for p in UPLINKS]
    dexec(src.name, f"ping -q -c 200 -i {interval} {dst_ip}", check=False)
    after = [nos.tx_bytes("leaf1", p) for p in UPLINKS]
    used = UPLINKS[0] if after[0] - before[0] >= after[1] - before[1] else UPLINKS[1]

    dexec(src.name, "rm -f /tmp/ping.out", check=False)
    subprocess.Popen(["docker", "exec", container(src.name), "sh", "-c",
                      f"ping -c 1500 -i {interval} {dst_ip} > /tmp/ping.out 2>&1"])
    time.sleep(3)
    nos.set_link("leaf1", used, up=False)
    try:
        time.sleep(10)
    finally:
        nos.set_link("leaf1", used, up=True)
    _wait(lambda: "packet loss" in dexec(src.name, "cat /tmp/ping.out", check=False),
          timeout=30, step=1)
    out = dexec(src.name, "cat /tmp/ping.out")
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", out)
    assert m, out[-500:]
    sent, recv = int(m.group(1)), int(m.group(2))
    lost = sent - recv
    convergence_s = lost * interval
    path = write_result(
        "net", f"convergence-{nos.name}",
        config={"nos": nos.name, "failed_link": f"leaf1:{used}", "ping_interval_s": interval,
                "src": src.name, "dst": dst.name, "bfd_tx_ms": 100, "bfd_multiplier": 3},
        metrics={"packets_sent": sent, "packets_lost": lost,
                 "convergence_s": round(convergence_s, 3)},
        hardware={"instance_type": "containerlab", "region": "local", "cloud": "local"},
        notes="Convergence estimated as packets lost x ping interval.",
    )
    print(f"\nconvergence: lost {lost}/{sent} -> {convergence_s:.2f}s ({path})")
    assert convergence_s < 3.0, f"convergence too slow: {convergence_s:.2f}s"
