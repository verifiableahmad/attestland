# ADR 011: An AWS bare-metal instance as the Linux/KVM lab host

- Status: accepted
- Date: 2026-11-12
- Phase / section: Phase 1, spec 1.1 and 2.1

## Decision

`infra/lab-host` creates one `c5.metal` (96 vCPU, 192 GiB, ~$4.08/h on demand
in us-east-1) running Ubuntu 22.04 directly on the hardware, in its own VPC,
reachable only over SSH from `admin_cidrs`. `make lab-host-up` creates it,
waits for cloud-init and copies the working tree; `make lab-up`,
`make lab-test` and `make provision` then run on it. It carries the project
tags, so the 8-hour reaper deletes it.

## Context

The SR Linux fabric and the L3 VNI test need a real Linux kernel (ADR 004), and
the provisioning simulation needs `/dev/kvm` (external-deps item 4). The Mac
has neither.

Options, on-demand prices from 2026-11-12:

- A nested-virtualization VM (Azure D8s_v5 $0.38/h, AWS m8i.2xlarge
  $0.42/h). About a tenth of the price and enough for both jobs.
- Azure: no self-service bare metal. BareMetal Infrastructure is for SAP HANA,
  Oracle and Nutanix only; a Dedicated Host ($4.22/h and up) still runs Azure
  VMs on Hyper-V.
- AWS `*.metal`: the cheapest x86 type is `c5.metal`. Graviton metal is
  cheaper but arm64, and the provisioning image is amd64.

Bare metal was chosen to learn the stack first hand: KVM runs with no
hypervisor below it, and the host's real CPU, NICs and NVMe are visible.

## Consequences

- AWS still boots the host itself from an AMI through Nitro; its firmware,
  BMC and PXE stay hidden. The from-scratch build (Redfish, DHCP/iPXE,
  Tinkerbell, image write) is seen on the simulated nodes inside it.
- About $33 per 8-hour session; `keep=true` costs ~$98 a day.
- Needs standard on-demand vCPU quota (A, C, D, H, I, M, R, T, Z; Service
  Quotas `L-1216C47A`) of at least 96 in us-east-1, separate from the G and VT
  quota. The host must stay in the reaper role's region.
- A nested-virtualization VM stays the cheaper fallback: change
  `instance_type` and drop the `.metal` validation.
