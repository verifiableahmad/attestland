"""FRR renderer: the laptop fallback, and the routing sections SONiC reuses.

Same fabric model as the SONiC configs: eBGP underlay with BFD and ECMP,
BGP EVPN with VXLAN, two tenants with an L2 VNI and an L3 VNI each. SONiC's
routing stack is FRR, so gen_sonic.py applies spine_bgp/leaf_bgp unchanged
through vtysh; here they sit in a full frr.conf on a plain FRR container.

The FRR data plane is the Linux kernel, so net.sh builds the bridges, VXLAN
devices and VRFs. On kernels without VRF support (Docker Desktop's LinuxKit
kernel) net.sh skips the L3 VNI and the tenant gateways, which keeps tenants
isolated but leaves no type-5 routes; the lab tests report that as a skip
with the reason.
"""

from __future__ import annotations

from pathlib import Path

import fabric

ANYCAST_MAC = "00:00:5e:00:01:01"

DAEMONS = """\
zebra=yes
bgpd=yes
bfdd=yes
staticd=yes
ospfd=no
ospf6d=no
ripd=no
ripngd=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
fabricd=no
vrrpd=no
pathd=no
vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
bgpd_options="   -A 127.0.0.1"
bfdd_options="   -A 127.0.0.1"
staticd_options="-A 127.0.0.1"
"""


def vrf_name(t: fabric.Tenant) -> str:
    return f"vrf-{t.name}"


BFD_PROFILE = ["bfd", " profile fast", "  transmit-interval 100", "  receive-interval 100",
               "  detect-multiplier 3", " exit", "exit", "!"]


def _common(node: fabric.Node) -> list[str]:
    """frr.conf header and addressing; on SONiC the addresses come from `config`."""
    lines = ["frr defaults datacenter", f"hostname {node.name}",
             "log stdout informational", "service integrated-vtysh-config", "!",
             "interface lo", f" ip address {node.loopback}", "exit", "!"]
    for port, addr in node.ifaces.items():
        lines += [f"interface {port}", f" ip address {addr}", "exit", "!"]
    return lines


def bgp_head(asn: int, loopback: str, neighbors, group: str,
             networks: list[str] = ()) -> list[str]:
    """eBGP underlay: one peer group, BFD, ECMP across ASNs, the loopback (and networks) advertised."""
    lo = loopback.split("/")[0]
    lines = [f"router bgp {asn}",
             f" bgp router-id {lo}",
             " no bgp ebgp-requires-policy",
             " no bgp default ipv4-unicast",
             " bgp bestpath as-path multipath-relax",
             f" neighbor {group} peer-group",
             f" neighbor {group} remote-as external",
             f" neighbor {group} bfd profile fast",
             f" neighbor {group} timers 3 9",
             f" neighbor {group} timers connect 2"]
    for peer, _ in neighbors:
        lines.append(f" neighbor {peer} peer-group {group}")
    lines += [" !", " address-family ipv4 unicast",
              f"  network {loopback}"]
    lines += [f"  network {n}" for n in networks]
    lines += [f"  neighbor {group} activate",
              "  maximum-paths 8",
              " exit-address-family"]
    return lines


def spine_bgp(node: fabric.Node) -> list[str]:
    """Spine routing: underlay plus EVPN transit (next hop kept, so VTEPs talk directly)."""
    lines = BFD_PROFILE + bgp_head(node.asn, node.loopback, node.bgp_neighbors, "LEAVES")
    lines += [" address-family l2vpn evpn",
              "  neighbor LEAVES activate",
              "  neighbor LEAVES attribute-unchanged next-hop",
              " exit-address-family", "exit", "!"]
    return lines


def leaf_bgp(node: fabric.Node, vrf=vrf_name) -> list[str]:
    """Leaf routing: underlay, EVPN with fixed route targets, one BGP VRF per tenant L3 VNI."""
    rt = fabric.OVERLAY_ASN
    lines = list(BFD_PROFILE)
    for t in fabric.TENANTS:
        lines += [f"vrf {vrf(t)}", f" vni {t.l3_vni}", "exit-vrf", "!"]
    lines += bgp_head(node.asn, node.loopback, node.bgp_neighbors, "SPINES")
    lines += [" address-family l2vpn evpn",
              "  neighbor SPINES activate",
              "  advertise-all-vni"]
    for t in fabric.TENANTS:
        lines += [f"  vni {t.l2_vni}",
                  f"   route-target import {rt}:{t.l2_vni}",
                  f"   route-target export {rt}:{t.l2_vni}",
                  "  exit-vni"]
    lines += [" exit-address-family", "exit", "!"]
    for t in fabric.TENANTS:
        lines += [f"router bgp {node.asn} vrf {vrf(t)}",
                  f" bgp router-id {node.loopback.split('/')[0]}",
                  " address-family ipv4 unicast", "  redistribute connected",
                  " exit-address-family",
                  " address-family l2vpn evpn", "  advertise ipv4 unicast",
                  f"  route-target import {rt}:{t.l3_vni}",
                  f"  route-target export {rt}:{t.l3_vni}",
                  " exit-address-family", "exit", "!"]
    return lines


def render_spine(node: fabric.Node) -> str:
    return "\n".join(_common(node) + spine_bgp(node)) + "\n"


def render_leaf(node: fabric.Node) -> str:
    return "\n".join(_common(node) + leaf_bgp(node)) + "\n"


def render_leaf_net(node: fabric.Node) -> str:
    """Kernel data plane for a leaf: bridges, VXLAN devices, optional VRFs."""
    lo = node.loopback.split("/")[0]
    sh = ["#!/bin/sh", "# Generated: kernel data plane for the FRR leaf.", "set -u",
          "sysctl -qw net.ipv4.ip_forward=1",
          "sysctl -qw net.ipv4.fib_multipath_hash_policy=1",
          "sysctl -qw net.ipv4.conf.all.rp_filter=0",
          "VRF=1",
          "ip link add vrf-probe type vrf table 999 2>/dev/null && ip link del vrf-probe || VRF=0",
          'echo "vrf-support=$VRF" > /tmp/vrf-support']
    for t in fabric.TENANTS:
        acc = f"eth{t.host_port}"
        br, vx = f"br{t.l2_vni}", f"vx{t.l2_vni}"
        sh += [f"# tenant {t.name}",
               f"ip link add {br} type bridge",
               f"ip link set {br} addrgenmode none 2>/dev/null || true",
               f"ip link add {vx} type vxlan id {t.l2_vni} dstport 4789 local {lo} nolearning",
               f"ip link set {vx} master {br} addrgenmode none",
               f"ip link set {vx} type bridge_slave neigh_suppress on learning off",
               f"ip link set {acc} master {br}",
               f"ip link set {acc} up; ip link set {vx} up; ip link set {br} up",
               'if [ "$VRF" = 1 ]; then',
               f"  ip link add vrf-{t.name} type vrf table {t.l3_evi}",
               f"  ip link set vrf-{t.name} up",
               f"  ip link add br{t.l3_vni} type bridge",
               f"  ip link set br{t.l3_vni} master vrf-{t.name} addrgenmode none",
               f"  ip link add vx{t.l3_vni} type vxlan id {t.l3_vni} dstport 4789 local {lo} nolearning",
               f"  ip link set vx{t.l3_vni} master br{t.l3_vni} addrgenmode none",
               f"  ip link set vx{t.l3_vni} up; ip link set br{t.l3_vni} up",
               f"  ip link set {br} master vrf-{t.name}",
               f"  ip link set {br} address {ANYCAST_MAC}",
               f"  ip addr add {t.gateway} dev {br}",
               "fi"]
    return "\n".join(sh) + "\n"


def render_topology(fab: fabric.Fabric, v: dict[str, str]) -> str:
    out = ["# Generated by gen.py (FRR, laptop fallback). Do not edit by hand.",
           "name: al-fabric",
           "mgmt:", "  network: al-fabric-mgmt", "  ipv4-subnet: 172.30.30.0/24",
           "topology:", "  kinds:", "    linux:", f"      image: {v['LAB_HOST_IMAGE']}",
           "  nodes:"]
    for n in fab.spines + fab.leaves:
        binds = [f"configs/frr/{n.name}/daemons:/etc/frr/daemons",
                 f"configs/frr/{n.name}/frr.conf:/etc/frr/frr.conf",
                 f"configs/frr/{n.name}/vtysh.conf:/etc/frr/vtysh.conf"]
        execs = ["sysctl -qw net.ipv4.fib_multipath_hash_policy=1"]
        if n.role == "leaf":
            binds.append(f"configs/frr/{n.name}/net.sh:/net.sh")
            execs = ["sh /net.sh"]
        out += [f"    {n.name}:", "      kind: linux", f"      image: {v['FRR_IMAGE']}",
                f"      labels: {{role: {n.role}, nos: frr}}", "      binds:"]
        out += [f"        - {b}" for b in binds]
        out += ["      exec:"] + [f"        - {e}" for e in execs]
    for h in fab.hosts:
        gw = h.tenant.gateway.split("/")[0]
        out += [f"    {h.name}:", "      kind: linux",
                f"      labels: {{role: host, tenant: {h.tenant.name}, leaf: {h.leaf}}}",
                "      exec:", "        - ip link set eth1 mtu 1450",
                f"        - ip addr add {h.address} dev eth1",
                f"        - ip route add 172.16.0.0/16 via {gw} dev eth1"]
    out.append("  links:")
    for a, b in fab.links:
        out.append(f"    - endpoints: [\"{a}\", \"{b}\"]")
    return "\n".join(out) + "\n"


def write_all(out_dir: Path, topo_path: Path, v: dict[str, str]) -> int:
    fab = fabric.build()
    for n in fab.spines + fab.leaves:
        d = out_dir / "frr" / n.name
        d.mkdir(parents=True, exist_ok=True)
        (d / "daemons").write_text(DAEMONS)
        (d / "vtysh.conf").write_text("service integrated-vtysh-config\n")
        if n.role == "spine":
            (d / "frr.conf").write_text(render_spine(n))
        else:
            (d / "frr.conf").write_text(render_leaf(n))
            (d / "net.sh").write_text(render_leaf_net(n))
    topo_path.write_text(render_topology(fab, v))
    return len(fab.spines) + len(fab.leaves)
