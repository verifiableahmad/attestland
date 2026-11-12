"""Offline checks on the fabric model and generated configs (no lab needed)."""

from __future__ import annotations

from ipaddress import IPv4Interface

import pytest

import fabric
import gen_frr
import gen_sonic

pytestmark = pytest.mark.offline


def test_topology_shape():
    f = fabric.build()
    assert len(f.spines) == 2 and len(f.leaves) == 4 and len(f.hosts) == 8
    for leaf in f.leaves:
        assert sum(h.leaf == leaf.name for h in f.hosts) == 2


def test_p2p_links_are_valid_slash31_pairs():
    f = fabric.build()
    seen = set()
    for s in f.spines:
        for port, addr in s.ifaces.items():
            ip = IPv4Interface(addr)
            assert ip.network.prefixlen == 31
            assert ip.network not in seen
            seen.add(ip.network)
    for l in f.leaves:
        for port, addr in l.ifaces.items():
            assert IPv4Interface(addr).network in seen


def test_one_asn_per_leaf_shared_spine_asn():
    f = fabric.build()
    assert len({l.asn for l in f.leaves}) == len(f.leaves)
    assert {s.asn for s in f.spines} == {fabric.SPINE_ASN}


def test_vni_and_evi_are_unique():
    vnis = [v for t in fabric.TENANTS for v in (t.l2_vni, t.l3_vni)]
    evis = [e for t in fabric.TENANTS for e in (t.l2_evi, t.l3_evi)]
    assert len(set(vnis)) == len(vnis)
    assert len(set(evis)) == len(evis)


def test_sonic_port_mapping():
    assert gen_sonic.sonic_port("eth1") == "Ethernet0"
    assert gen_sonic.sonic_port("eth4") == "Ethernet12"


def test_ports_fit_docker_sonic_vs():
    # docker-sonic-vs (Force10-S6000) has 32 ports and only creates those whose ethN
    # exists, so every switch uses eth1..ethN with no gaps.
    f, r = fabric.build(), fabric.rail_build()
    for name, ports in [(n.name, n.ifaces) for n in f.spines + f.leaves] + \
                       [(n["name"], n["ifaces"]) for n in r["spines"] + r["leaves"]]:
        used = {int(p[3:]) for p in ports}
        if name.startswith("leaf"):
            used |= {t.host_port for t in fabric.TENANTS}
        assert used == set(range(1, len(used) + 1)) and max(used) <= 32, (name, sorted(used))


def test_sonic_leaf_has_tenants_vteps_and_bfd():
    f = fabric.build()
    setup, frr = gen_sonic.render_leaf(f.leaves[0])
    for t in fabric.TENANTS:
        vrf, l2, l3 = gen_sonic.vrf_name(t), gen_sonic.l2_vlan(t), gen_sonic.l3_vlan(t)
        assert f"config vxlan map add vtep1 {l2} {t.l2_vni}" in setup
        assert f"config vrf add_vrf_vni_map {vrf} {t.l3_vni}" in setup
        assert f"config interface ip add Vlan{l2} {t.gateway}" in setup
        assert f"vrf {vrf}\n vni {t.l3_vni}" in frr
        assert f"router bgp {f.leaves[0].asn} vrf {vrf}" in frr
    assert "config vxlan evpn_nvo add nvo1 vtep1" in setup
    assert "neighbor SPINES bfd profile fast" in frr and "maximum-paths 8" in frr
    assert setup.rstrip().endswith(f"touch {gen_sonic.READY}")


def test_sonic_leaf_follows_hld_order():
    # SONiC EVPN HLD: VLAN before its VNI map; the L3 VNI's VLAN map before the VRF-VNI map.
    setup, _ = gen_sonic.render_leaf(fabric.build().leaves[0])
    pos = {line: i for i, line in enumerate(setup.splitlines())}
    for t in fabric.TENANTS:
        vrf, l3 = gen_sonic.vrf_name(t), gen_sonic.l3_vlan(t)
        assert pos[f"config vrf add {vrf}"] < pos[f"config vrf add_vrf_vni_map {vrf} {t.l3_vni}"]
        assert pos[f"config vlan add {l3}"] < pos[f"config vxlan map add vtep1 {l3} {t.l3_vni}"] \
            < pos[f"config vrf add_vrf_vni_map {vrf} {t.l3_vni}"]
    assert pos["vtysh -f /configs/frr.conf"] > max(pos[f"config vrf add_vrf_vni_map {gen_sonic.vrf_name(t)} {t.l3_vni}"]
                                                     for t in fabric.TENANTS)


def test_sonic_rail_leaf_advertises_gpu_links():
    cfgs, _ = gen_sonic.render_rail({"SONIC_VS_IMAGE": "x", "LAB_HOST_IMAGE": "y"})
    _, frr = cfgs["rail1"]
    assert "network 10.12.1.0/31" in frr and "network 10.12.1.2/31" in frr


def test_frr_leaf_route_targets_ignore_leaf_asn():
    # eBGP leaves have different ASNs; auto-derived RTs would never match.
    f = fabric.build()
    cfg = gen_frr.render_leaf(f.leaves[0])
    for t in fabric.TENANTS:
        assert f"route-target import {fabric.OVERLAY_ASN}:{t.l2_vni}" in cfg
        assert f"route-target import {fabric.OVERLAY_ASN}:{t.l3_vni}" in cfg


def test_rail_every_gpu_rail_lands_on_its_leaf():
    r = fabric.rail_build()
    for node in r["nodes"]:
        for nic in node["nics"]:
            assert nic["leaf"] == f"rail{nic['rail']}"
