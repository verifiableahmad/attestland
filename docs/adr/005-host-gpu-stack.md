# ADR 005: The host owns the driver, container toolkit and DCGM exporter

- Status: accepted
- Date: 2026-11-07
- Phase / section: Phase 2, spec 3.2 / 6.1

## Decision

The NVIDIA driver, container toolkit, DCGM host engine and DCGM exporter are
installed on the host by Ansible. The GPU Operator runs with
`driver.enabled=false`, `toolkit.enabled=false`, `dcgmExporter.enabled=false`
and provides the device plugin and GPU feature discovery.

## Context

Slurm (Enroot/Pyxis) and Kubernetes share each node; a node moves between
them with pool-move. The spec already puts the driver on the host and the
exporter in a systemd container. The spec text has the operator provide the
toolkit, but a second toolkit on the same host would fight the host one that
Enroot and podman need.

## Consequences

One toolkit configures containerd (`nvidia-ctk runtime configure`) and podman
(CDI). Upgrades happen in one place (versions.env → Ansible).
