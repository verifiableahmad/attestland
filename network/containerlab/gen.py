#!/usr/bin/env python3
"""Render SR Linux startup configs and containerlab topologies from fabric.py.

    python network/containerlab/gen.py --out network/containerlab/configs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fabric  # noqa: E402
import gen_frr  # noqa: E402

REPO = HERE.parents[1]


def _versions() -> dict[str, str]:
    out = {}
    for line in (REPO / "versions.env").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip()
    return out


# --- shared snippets ---------------------------------------------------------

def _routed_iface(port: str, addr: str) -> list[str]:
    return [
        f"set / interface {port} admin-state enable",
        f"set / interface {port} subinterface 0 admin-state enable",
        f"set / interface {port} subinterface 0 ipv4 admin-state enable",
        f"set / interface {port} subinterface 0 ipv4 address {addr}",
        f"set / network-instance default interface {port}.0",
    ]


def _loopback(addr: str) -> list[str]:
    return [
        "set / interface system0 admin-state enable",
        "set / interface system0 subinterface 0 admin-state enable",
        "set / interface system0 subinterface 0 ipv4 admin-state enable",
        f"set / interface system0 subinterface 0 ipv4 address {addr}",
        "set / network-instance default interface system0.0",
    ]


def _policies(loopback_net: str, extra_prefixes: list[str] = ()) -> list[str]:
    lines = [
        f"set / routing-policy prefix-set loopbacks prefix {loopback_net} mask-length-range 32..32",
        "set / routing-policy policy export-underlay default-action policy-result reject",
        "set / routing-policy policy export-underlay statement 10 match prefix prefix-set loopbacks",
        "set / routing-policy policy export-underlay statement 10 action policy-result accept",
        "set / routing-policy policy accept-all default-action policy-result accept",
    ]
    for i, p in enumerate(extra_prefixes):
        lines += [
            f"set / routing-policy prefix-set extra{i} prefix {p} mask-length-range 24..32",
            f"set / routing-policy policy export-underlay statement {20 + i} match prefix prefix-set extra{i}",
            f"set / routing-policy policy export-underlay statement {20 + i} action policy-result accept",
        ]
    return lines


def _underlay_bgp(asn: int, router_id: str, neighbors, ports) -> list[str]:
    bgp = "set / network-instance default protocols bgp"
    lines = [
        "set / network-instance default type default",
        "set / network-instance default admin-state enable",
        f"{bgp} admin-state enable",
        f"{bgp} autonomous-system {asn}",
        f"{bgp} router-id {router_id}",
        f"{bgp} afi-safi ipv4-unicast admin-state enable",
        f"{bgp} afi-safi ipv4-unicast multipath allow-multiple-as true",
        f"{bgp} afi-safi ipv4-unicast multipath ebgp maximum-paths 8",
        f"{bgp} group underlay admin-state enable",
        f"{bgp} group underlay export-policy [ export-underlay ]",
        f"{bgp} group underlay import-policy [ accept-all ]",
        f"{bgp} group underlay failure-detection enable-bfd true",
        f"{bgp} group underlay failure-detection fast-failover true",
        f"{bgp} group underlay afi-safi ipv4-unicast admin-state enable",
        f"{bgp} group underlay afi-safi evpn admin-state disable",
    ]
    for peer, peer_as in neighbors:
        lines += [
            f"{bgp} neighbor {peer} admin-state enable",
            f"{bgp} neighbor {peer} peer-group underlay",
            f"{bgp} neighbor {peer} peer-as {peer_as}",
        ]
    for port in ports:
        lines += [
            f"set / bfd subinterface {port}.0 admin-state enable",
            f"set / bfd subinterface {port}.0 desired-minimum-transmit-interval 100000",
            f"set / bfd subinterface {port}.0 required-minimum-receive 100000",
            f"set / bfd subinterface {port}.0 detection-multiplier 3",
        ]
    return lines


def _overlay_bgp(local: str, peers: list[str], reflector: bool) -> list[str]:
    bgp = "set / network-instance default protocols bgp"
    lines = [
        f"{bgp} group overlay admin-state enable",
        f"{bgp} group overlay peer-as {fabric.OVERLAY_ASN}",
        f"{bgp} group overlay local-as as-number {fabric.OVERLAY_ASN}",
        f"{bgp} group overlay afi-safi evpn admin-state enable",
        f"{bgp} group overlay afi-safi ipv4-unicast admin-state disable",
        f"{bgp} group overlay transport local-address {local}",
        f"{bgp} group overlay timers connect-retry 1",
        f"{bgp} group overlay timers minimum-advertisement-interval 1",
    ]
    if reflector:
        lines += [
            f"{bgp} group overlay route-reflector client true",
            f"{bgp} group overlay route-reflector cluster-id {local}",
        ]
    for p in peers:
        lines += [
            f"{bgp} neighbor {p} admin-state enable",
            f"{bgp} neighbor {p} peer-group overlay",
        ]
    return lines


# --- per-role renderers ------------------------------------------------------

def render_spine(node: fabric.Node, leaves: list[fabric.Node]) -> str:
    lo = node.loopback.split("/")[0]
    lines = [f"# {node.name}: spine, AS {node.asn}, EVPN route reflector (generated)"]
    lines += _loopback(node.loopback)
    for port, addr in node.ifaces.items():
        lines += _routed_iface(port, addr)
    lines += _policies("10.0.0.0/16")
    lines += _underlay_bgp(node.asn, lo, node.bgp_neighbors, node.ifaces)
    lines += _overlay_bgp(lo, [l.loopback.split("/")[0] for l in leaves], reflector=True)
    return "\n".join(lines) + "\n"


def render_leaf(node: fabric.Node, spines: list[fabric.Node]) -> str:
    lo = node.loopback.split("/")[0]
    lines = [f"# {node.name}: leaf, AS {node.asn}, VTEP {lo} (generated)"]
    lines += _loopback(node.loopback)
    for port, addr in node.ifaces.items():
        lines += _routed_iface(port, addr)
    lines += _policies("10.0.0.0/16")
    lines += _underlay_bgp(node.asn, lo, node.bgp_neighbors, node.ifaces)
    lines += _overlay_bgp(lo, [s.loopback.split("/")[0] for s in spines], reflector=False)
    for t in fabric.TENANTS:
        lines += _tenant(t)
    return "\n".join(lines) + "\n"


def _tenant(t: fabric.Tenant) -> list[str]:
    acc = f"ethernet-1/{t.host_port}"
    l2, l3 = f"{t.name}-l2", t.name
    irb = f"irb0.{t.l2_evi}"
    rt = f"target:{fabric.OVERLAY_ASN}"
    return [
        f"# tenant {t.name}: L2 VNI {t.l2_vni}, L3 VNI {t.l3_vni}",
        # access port
        f"set / interface {acc} admin-state enable",
        f"set / interface {acc} subinterface 0 type bridged",
        f"set / interface {acc} subinterface 0 admin-state enable",
        # anycast gateway
        "set / interface irb0 admin-state enable",
        f"set / interface irb0 subinterface {t.l2_evi} admin-state enable",
        f"set / interface irb0 subinterface {t.l2_evi} ipv4 admin-state enable",
        f"set / interface irb0 subinterface {t.l2_evi} ipv4 address {t.gateway} anycast-gw true",
        f"set / interface irb0 subinterface {t.l2_evi} ipv4 arp learn-unsolicited true",
        f"set / interface irb0 subinterface {t.l2_evi} ipv4 arp host-route populate dynamic",
        f"set / interface irb0 subinterface {t.l2_evi} ipv4 arp evpn advertise dynamic",
        f"set / interface irb0 subinterface {t.l2_evi} anycast-gw virtual-router-id 1",
        # VXLAN tunnel interfaces
        f"set / tunnel-interface vxlan1 vxlan-interface {t.l2_evi} type bridged",
        f"set / tunnel-interface vxlan1 vxlan-interface {t.l2_evi} ingress vni {t.l2_vni}",
        f"set / tunnel-interface vxlan1 vxlan-interface {t.l2_evi} egress source-ip use-system-ipv4-address",
        f"set / tunnel-interface vxlan1 vxlan-interface {t.l3_evi} type routed",
        f"set / tunnel-interface vxlan1 vxlan-interface {t.l3_evi} ingress vni {t.l3_vni}",
        f"set / tunnel-interface vxlan1 vxlan-interface {t.l3_evi} egress source-ip use-system-ipv4-address",
        # MAC-VRF (L2 VNI, type-2 routes)
        f"set / network-instance {l2} type mac-vrf",
        f"set / network-instance {l2} admin-state enable",
        f"set / network-instance {l2} interface {acc}.0",
        f"set / network-instance {l2} interface {irb}",
        f"set / network-instance {l2} vxlan-interface vxlan1.{t.l2_evi}",
        f"set / network-instance {l2} protocols bgp-evpn bgp-instance 1 admin-state enable",
        f"set / network-instance {l2} protocols bgp-evpn bgp-instance 1 vxlan-interface vxlan1.{t.l2_evi}",
        f"set / network-instance {l2} protocols bgp-evpn bgp-instance 1 evi {t.l2_evi}",
        f"set / network-instance {l2} protocols bgp-evpn bgp-instance 1 routes bridge-table mac-ip advertise-arp-nd-only-with-mac-table-entry true",
        f"set / network-instance {l2} protocols bgp-vpn bgp-instance 1 route-target export-rt {rt}:{t.l2_evi}",
        f"set / network-instance {l2} protocols bgp-vpn bgp-instance 1 route-target import-rt {rt}:{t.l2_evi}",
        # IP-VRF (L3 VNI, type-5 routes)
        f"set / network-instance {l3} type ip-vrf",
        f"set / network-instance {l3} admin-state enable",
        f"set / network-instance {l3} interface {irb}",
        f"set / network-instance {l3} vxlan-interface vxlan1.{t.l3_evi}",
        f"set / network-instance {l3} protocols bgp-evpn bgp-instance 1 admin-state enable",
        f"set / network-instance {l3} protocols bgp-evpn bgp-instance 1 vxlan-interface vxlan1.{t.l3_evi}",
        f"set / network-instance {l3} protocols bgp-evpn bgp-instance 1 evi {t.l3_evi}",
        f"set / network-instance {l3} protocols bgp-vpn bgp-instance 1 route-target export-rt {rt}:{t.l3_evi}",
        f"set / network-instance {l3} protocols bgp-vpn bgp-instance 1 route-target import-rt {rt}:{t.l3_evi}",
    ]


def render_topology(fab: fabric.Fabric, v: dict[str, str]) -> str:
    out = [
        "# Generated by gen.py from fabric.py. Do not edit by hand.",
        "name: al-fabric",
        "mgmt:",
        "  network: al-fabric-mgmt",
        "  ipv4-subnet: 172.30.30.0/24",
        "topology:",
        "  kinds:",
        "    nokia_srlinux:",
        f"      image: {v['SRLINUX_IMAGE']}",
        "      type: ixrd2l",
        "    linux:",
        f"      image: {v['LAB_HOST_IMAGE']}",
        "  nodes:",
    ]
    for n in fab.spines + fab.leaves:
        out += [f"    {n.name}:",
                "      kind: nokia_srlinux",
                f"      startup-config: configs/{n.name}.cfg",
                f"      labels: {{role: {n.role}}}"]
    for h in fab.hosts:
        gw = h.tenant.gateway.split("/")[0]
        out += [f"    {h.name}:",
                "      kind: linux",
                f"      labels: {{role: host, tenant: {h.tenant.name}, leaf: {h.leaf}}}",
                "      exec:",
                f"        - ip link set eth1 mtu 1450",
                f"        - ip addr add {h.address} dev eth1",
                f"        - ip route add 172.16.0.0/16 via {gw} dev eth1"]
    out.append("  links:")
    for a, b in fab.links:
        out.append(f"    - endpoints: [\"{a}\", \"{b}\"]")
    return "\n".join(out) + "\n"


def render_rail(v: dict[str, str]) -> tuple[dict[str, str], str]:
    r = fabric.rail_build()
    cfgs = {}
    for sp in r["spines"]:
        lines = [f"# {sp['name']}: rail spine (generated)"] + _loopback(sp["loopback"])
        for port, addr in sp["ifaces"].items():
            lines += _routed_iface(port, addr)
        lines += _policies("10.10.0.0/16", ["10.12.0.0/16"])
        lines += _underlay_bgp(sp["asn"], sp["loopback"].split("/")[0], sp["neighbors"], sp["ifaces"])
        cfgs[sp["name"]] = "\n".join(lines) + "\n"
    for lf in r["leaves"]:
        lines = [f"# {lf['name']}: rail leaf, pure L3 (generated)"] + _loopback(lf["loopback"])
        for port, addr in lf["ifaces"].items():
            lines += _routed_iface(port, addr)
        lines += _policies("10.10.0.0/16", ["10.12.0.0/16"])
        uplinks = [p for p in lf["ifaces"] if int(p.split("/")[1]) >= fabric.UPLINK_PORT_BASE]
        lines += _underlay_bgp(lf["asn"], lf["loopback"].split("/")[0], lf["neighbors"], uplinks)
        cfgs[lf["name"]] = "\n".join(lines) + "\n"
    topo = [
        "# Generated by gen.py: rail-optimised pure-L3 backend (stretch).",
        "name: al-rail",
        "mgmt:",
        "  network: al-rail-mgmt",
        "  ipv4-subnet: 172.30.31.0/24",
        "topology:",
        "  kinds:",
        "    nokia_srlinux:",
        f"      image: {v['SRLINUX_IMAGE']}",
        "      type: ixrd2l",
        "    linux:",
        f"      image: {v['LAB_HOST_IMAGE']}",
        "  nodes:",
    ]
    for n in r["spines"] + r["leaves"]:
        topo += [f"    {n['name']}:", "      kind: nokia_srlinux",
                 f"      startup-config: configs/rail/{n['name']}.cfg"]
    for node in r["nodes"]:
        topo += [f"    {node['name']}:", "      kind: linux", "      exec:"]
        for nic in node["nics"]:
            topo += [f"        - ip addr add {nic['address']} dev {nic['iface']}",
                     f"        - ip route add 10.12.0.0/16 via {nic['gateway']} dev {nic['iface']} metric {nic['rail']}"]
    topo.append("  links:")
    for a, b in r["links"]:
        topo.append(f"    - endpoints: [\"{a}\", \"{b}\"]")
    return cfgs, "\n".join(topo) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "configs")
    args = ap.parse_args()
    v = _versions()
    fab = fabric.build()
    args.out.mkdir(parents=True, exist_ok=True)
    for s in fab.spines:
        (args.out / f"{s.name}.cfg").write_text(render_spine(s, fab.leaves))
    for l in fab.leaves:
        (args.out / f"{l.name}.cfg").write_text(render_leaf(l, fab.spines))
    (HERE / "fabric.clab.yml").write_text(render_topology(fab, v))
    rail_cfgs, rail_topo = render_rail(v)
    (args.out / "rail").mkdir(exist_ok=True)
    for name, cfg in rail_cfgs.items():
        (args.out / "rail" / f"{name}.cfg").write_text(cfg)
    (HERE / "rail.clab.yml").write_text(rail_topo)
    n_frr = gen_frr.write_all(args.out, HERE / "fabric-frr.clab.yml", v)
    print(f"wrote {len(fab.spines) + len(fab.leaves)} SR Linux configs, {n_frr} FRR configs, "
          f"{len(rail_cfgs)} rail configs, 3 topologies")


if __name__ == "__main__":
    main()
