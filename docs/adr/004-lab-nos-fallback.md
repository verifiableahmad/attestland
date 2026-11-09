# ADR 004: FRR fallback for the lab; SR Linux stays the default

- Status: accepted
- Date: 2026-11-07
- Phase / section: Phase 1, spec 2.1

## Decision

`network/containerlab/gen.py` renders the same fabric for SR Linux (default)
and FRR (`LAB_NOS=frr`). The lab tests use one driver per NOS.

## Context

On Docker Desktop for macOS (LinuxKit kernel 7.0, arm64), SR Linux 25.7.2's
`net_inst_mgr` crashes at boot ("creating veth gway-1 ... File exists"), even
as a single node, so its configs never commit. The same kernel has no VRF
support, so FRR there can run the underlay, EVPN type-2 and VXLAN but not
the tenant L3 VNIs (type-5). The SR Linux configs were validated line by line
with `commit validate` on a running node before the crash path was hit.

## Consequences

On a laptop: 13 of 14 lab tests pass with FRR; the type-5 test skips with the
reason. Full spec coverage (SR Linux, L3 VNIs) needs a Linux host; CI runs
it on an Ubuntu runner (`.github/workflows/ci.yml`, job `lab`).
