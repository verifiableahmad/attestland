"""Single source of truth for the lab fabric: names, ASNs, links and addresses.

gen.py renders the SONiC and FRR configs and the containerlab topologies from
this model; the tests read it to know what to expect. Ports are the containerlab
wire names (eth1, eth2, ...); gen_sonic.sonic_port maps them to SONiC's
Ethernet0, Ethernet4, ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Interface

SPINE_ASN = 65100
LEAF_ASN_BASE = 65100          # leaf n gets 65100 + n
OVERLAY_ASN = 65500            # EVPN route targets (EVPN rides the eBGP underlay sessions)
N_SPINES = 2
N_LEAVES = 4
# Leaf eth3, eth4 go to the spines. docker-sonic-vs creates a front-panel port
# only for each ethN it finds at boot, so the lab keeps port numbers contiguous.
UPLINK_PORT_BASE = 3


@dataclass(frozen=True)
class Tenant:
    name: str
    subnet_prefix: str          # first three octets, e.g. "172.16.10"
    l2_vni: int
    l3_vni: int
    host_port: int              # leaf access port the tenant's host uses

    @property
    def gateway(self) -> str:
        return f"{self.subnet_prefix}.1/24"

    @property
    def subnet(self) -> str:
        return f"{self.subnet_prefix}.0/24"

    @property
    def l2_evi(self) -> int:
        return self.l2_vni // 1000          # 10010 -> 10

    @property
    def l3_evi(self) -> int:
        return self.l3_vni // 100           # 10100 -> 101


TENANTS = (
    Tenant("red", "172.16.10", l2_vni=10010, l3_vni=10100, host_port=1),
    Tenant("blue", "172.16.20", l2_vni=20010, l3_vni=20100, host_port=2),
)


@dataclass
class Node:
    name: str
    role: str                   # spine | leaf | host
    asn: int | None = None
    loopback: str | None = None
    ifaces: dict[str, str] = field(default_factory=dict)   # port -> addr/len
    bgp_neighbors: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Host:
    name: str
    leaf: str
    port: int
    tenant: Tenant
    address: str                # addr/len


@dataclass
class Fabric:
    spines: list[Node]
    leaves: list[Node]
    hosts: list[Host]
    links: list[tuple[str, str]]   # ("node:port", "node:port")


def spine_name(s: int) -> str:
    return f"spine{s}"


def leaf_name(l: int) -> str:
    return f"leaf{l}"


def p2p(spine: int, leaf: int) -> tuple[IPv4Interface, IPv4Interface]:
    """/31 per spine-leaf link: 10.1.<spine>.<2(leaf-1)>; spine even, leaf odd."""
    base = 2 * (leaf - 1)
    return (IPv4Interface(f"10.1.{spine}.{base}/31"),
            IPv4Interface(f"10.1.{spine}.{base + 1}/31"))


def build() -> Fabric:
    spines = [Node(spine_name(s), "spine", SPINE_ASN, f"10.0.0.{s}/32")
              for s in range(1, N_SPINES + 1)]
    leaves = [Node(leaf_name(l), "leaf", LEAF_ASN_BASE + l, f"10.0.1.{l}/32")
              for l in range(1, N_LEAVES + 1)]
    links: list[tuple[str, str]] = []
    for s, spine in enumerate(spines, 1):
        for l, leaf in enumerate(leaves, 1):
            s_ip, l_ip = p2p(s, l)
            s_port = f"eth{l}"
            l_port = f"eth{UPLINK_PORT_BASE + s - 1}"
            spine.ifaces[s_port] = str(s_ip)
            leaf.ifaces[l_port] = str(l_ip)
            spine.bgp_neighbors.append((str(l_ip.ip), leaf.asn))
            leaf.bgp_neighbors.append((str(s_ip.ip), spine.asn))
            links.append((f"{spine.name}:{s_port}", f"{leaf.name}:{l_port}"))
    hosts: list[Host] = []
    for l, leaf in enumerate(leaves, 1):
        for t in TENANTS:
            h = Host(f"h{len(hosts) + 1}", leaf.name, t.host_port, t,
                     f"{t.subnet_prefix}.{10 + l}/24")
            hosts.append(h)
            links.append((f"{h.name}:eth1", f"{leaf.name}:eth{t.host_port}"))
    return Fabric(spines, leaves, hosts, links)


# --- Rail-optimised backend (stretch) ----------------------------------------
# Pure L3, no overlay: GPU r of every node lands on rail leaf r. Each GPU NIC
# gets a /31 to its rail leaf; leaves advertise those /31s to the spines.

RAIL_NODES = 2
RAIL_RAILS = 2
RAIL_SPINE_ASN = 65200


def rail_build() -> dict:
    spines = [{"name": f"rspine{s}", "asn": RAIL_SPINE_ASN,
               "loopback": f"10.10.0.{s}/32", "ifaces": {}, "neighbors": []}
              for s in range(1, 3)]
    leaves = [{"name": f"rail{r}", "asn": RAIL_SPINE_ASN + r,
               "loopback": f"10.10.1.{r}/32", "ifaces": {}, "neighbors": []}
              for r in range(1, RAIL_RAILS + 1)]
    links, gpus = [], []
    for s, sp in enumerate(spines, 1):
        for r, lf in enumerate(leaves, 1):
            base = 2 * (r - 1)
            sp["ifaces"][f"eth{r}"] = f"10.11.{s}.{base}/31"
            lf["ifaces"][f"eth{UPLINK_PORT_BASE + s - 1}"] = f"10.11.{s}.{base + 1}/31"
            sp["neighbors"].append((f"10.11.{s}.{base + 1}", lf["asn"]))
            lf["neighbors"].append((f"10.11.{s}.{base}", sp["asn"]))
            links.append((f"{sp['name']}:eth{r}", f"{lf['name']}:eth{UPLINK_PORT_BASE + s - 1}"))
    # One container per GPU node; its eth<r> is the backend NIC of GPU r.
    for n in range(1, RAIL_NODES + 1):
        node = {"name": f"gpunode{n}", "nics": []}
        for r, lf in enumerate(leaves, 1):
            port = n
            leaf_ip = f"10.12.{r}.{2 * (n - 1)}/31"
            gpu_ip = f"10.12.{r}.{2 * (n - 1) + 1}/31"
            lf["ifaces"][f"eth{port}"] = leaf_ip
            node["nics"].append({"iface": f"eth{r}", "rail": r, "leaf": lf["name"],
                                 "address": gpu_ip, "gateway": leaf_ip.split("/")[0]})
            links.append((f"{node['name']}:eth{r}", f"{lf['name']}:eth{port}"))
        gpus.append(node)
    return {"spines": spines, "leaves": leaves, "nodes": gpus, "links": links}
