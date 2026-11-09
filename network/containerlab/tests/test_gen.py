"""Offline checks on the fabric model and generated configs (no lab needed)."""

from __future__ import annotations

from ipaddress import IPv4Interface

import pytest

import fabric
import gen
import gen_frr

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


def test_srl_leaf_config_has_tenants_and_bfd():
    f = fabric.build()
    cfg = gen.render_leaf(f.leaves[0], f.spines)
    for t in fabric.TENANTS:
        assert f"ingress vni {t.l2_vni}" in cfg
        assert f"ingress vni {t.l3_vni}" in cfg
        assert f"network-instance {t.name} type ip-vrf" in cfg
    assert "failure-detection enable-bfd true" in cfg
    assert "multipath ebgp maximum-paths" in cfg


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
