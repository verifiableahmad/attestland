# ADR 012: SONiC replaces SR Linux as the lab NOS; FRR stays the fallback

- Status: accepted
- Date: 2026-11-12
- Phase / section: Phase 1, spec 2.1
- Supersedes: ADR 004 (its FRR fallback stays)

## Decision

The containerlab fabric runs SONiC (`docker-sonic-vs`, kind `sonic-vs`) by
default. `network/containerlab/gen_sonic.py` renders, per switch, a
`setup.sh` (SONiC click CLI: addresses, VLANs, VTEP, VLAN-VNI and VRF-VNI
maps) and a `frr.conf` applied with `vtysh`. The BGP and BFD sections are
`gen_frr.py`'s, because SONiC's routing stack is FRR. `LAB_NOS=frr` keeps the
plain FRR fabric for laptops. Both NOSes render the same fabric model, with
the same eBGP underlay, BFD (100 ms × 3), EVPN route targets and tenants.

## Context

The lab stands in for the backend fabric of an on-prem GPU cluster, which
this project does not have. No cloud workload uses it. The lab should show
the switch software such a fabric would most likely run. In 2025 AI back-end
Ethernet switch sales, Celestica and NVIDIA together held about half and
Arista came third (Dell'Oro). White-box switches mostly run SONiC, NVIDIA
Spectrum runs Cumulus or SONiC, and Nokia, whose NOS is SR Linux, is a
minority vendor there. SR Linux was a sound practical choice (free, on a
registry, a full NOS) but does not represent this market.

Options:

- SR Linux (ADR 004): production NOS, easiest to run, uncommon in AI fabrics.
- Arista cEOS: production NOS common in AI fabrics, but its image needs an
  account and a manual download, which breaks a CI-reproducible lab.
- SONiC `sonic-vs`: the NOS most AI Ethernet fabrics run, open source.
- Plain FRR: the routing suite inside SONiC and Cumulus, without their
  configuration layer.

## Consequences

- `docker-sonic-vs` is a build artifact, not on a registry, and amd64 only.
  `make lab-image` (`network/containerlab/sonic-image.sh`) downloads the
  latest `SONIC_VS_BRANCH` build and loads it. The build server serves only
  the latest build of a branch, so the pin is `SONIC_VS_SHA256`. It is empty
  until the first download; set it from that run's output.
- On this Mac (arm64, LinuxKit kernel) SONiC does not run. `LAB_NOS=frr` is
  the laptop path. With the renumbered ports and containerlab 0.77.0 it
  passes 17 of 18 lab tests on Docker Desktop and skips type-5 for lack of
  VRF (ADR 004). Full coverage needs an x86 Linux host: the CI `lab` job, or the
  lab host (ADR 011).
- docker-sonic-vs creates a front-panel port only for each `ethN` present at
  boot (`eth1` → `Ethernet0`, step 4, at most 32). The fabric model now uses
  contiguous `ethN` ports. Leaf uplinks moved from 49/50 to `eth3`/`eth4`,
  which also renumbers the FRR fabric.
- EVPN runs over the eBGP underlay sessions, with route targets fixed to
  `65500:<vni>`, as the FRR fabric already did. The SR Linux design used a
  separate iBGP overlay with spines as route reflectors.
- docker-sonic-vs leaves `bgpd` stopped and has no `bfdd` program unless
  `frr_mgmt_framework_config` is set, so `setup.sh` starts both.
- The tenant gateway is the same address on every leaf, without SONiC's
  static anycast gateway MAC. The lab tests do not route through the
  gateway, but a production config would set that MAC.
- containerlab is pinned at 0.77.0, the first release the reference
  `sonic-vs` EVPN lab was checked on (netpilot-labs `sonic/evpn-l2vni`).
- The RoCE QoS reference is now a SONiC CONFIG_DB fragment
  (`roce/sonic-roce-qos.json`). It is still not applied to hardware.
- Not yet run: the SONiC fabric has not been deployed. Its first run on a
  Linux host (CI or lab host) validates the CLI sequence, the `bfdd` start
  and the VXLAN data plane in sonic-vs.
