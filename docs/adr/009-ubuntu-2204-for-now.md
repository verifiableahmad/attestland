# ADR 009: Ubuntu 22.04 for every VM until 24.04 is qualified for confidential GPU VMs

- Status: accepted
- Date: 2026-11-08
- Phase / section: Phases 2-5

## Decision

Every VM (GPU pool, control, confidential VM and its twin) stays on
Ubuntu 22.04 LTS: Canonical's `ubuntu-jammy-22.04` AMIs on AWS, `0001-com-ubuntu-confidential-vm-jammy` /
`22_04-lts-cvm` for Azure's confidential VM and the matching `22_04-lts-gen2` for the twin. The
confidential VM and the twin must run the same release, or the overhead comparison measures the OS.
Move all of them to 24.04 together once Azure qualifies it for NCC H100 v5 (not yet checked).

## What was checked (2026-11-08, updated 2026-11-10)

| Component | Result | Source |
| --- | --- | --- |
| Confidential VM, Azure NCC H100 v5 (SEV-SNP) | Azure's confidential GPU guide uses the 22.04 `22_04-lts-cvm` image. Whether 24.04 is qualified was not checked. | Azure confidential GPU guide |
| NVIDIA GPU Operator 26.7.x | Ubuntu 22.04, 24.04 and 26.04 LTS; Kubernetes 1.33-1.37 (we run 1.34); driver branches R580 and R595. 26.3 is deprecated and 25.10 and lower are end of support. | [NVIDIA platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) |
| Ubuntu support windows | 22.04: standard security maintenance to May 2027, ESM to May 2032. 24.04: May 2029 / May 2034. 26.04 (released April 2026): May 2031. | [Ubuntu release cycle](https://ubuntu.com/about/release-cycle) |
| Enroot 4.2.1, Pyxis 0.24.0 | Neither names a supported Ubuntu release. Enroot needs Linux >= 3.10 and, on Ubuntu, the AppArmor user-namespace setting disabled. Pyxis must be compiled against the Slurm release deployed. | [enroot](https://github.com/NVIDIA/enroot), [pyxis](https://github.com/NVIDIA/pyxis) |
| Slurm 25.05 | Built from source (Ubuntu 22.04 packages 21.08), so the OS does not limit it. Whether 25.05 is still a supported Slurm release was not confirmed. | not found |

## Consequences

- 22.04's standard support ends in about six months. Plan the move to 24.04 for the
  first half of 2027, or buy Ubuntu Pro for ESM.
- `GPU_OPERATOR_VERSION=v25.10.0` in `versions.env` is end of support. Bump it to 26.7.x
  and run `make up POOL=gpu` before relying on it (not done: untested).
- Ubuntu-specific names (`ubuntu2204` NVIDIA repo path, `ubuntu22.04` CUDA and DCGM images,
  `jammy` cloud image for the provisioning lab) all change together with the OS.

