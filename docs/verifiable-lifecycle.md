# Verifiable AI across the full lifecycle: architecture, options, limits

- Status: proposal, nothing here is built yet except what section 9 marks "exists"
- Date: 2026-11-16
- Reads on from: `docs/overview.md` (section 5.7), ADR 003, ADR 010

This document extends Attestland from "one signed receipt for one inference
batch on one confidential VM" to a chain of evidence that covers data, training,
evaluation, export, serving and audit, across AWS, Azure and (optionally) GCP.
It uses four families of technique: trusted execution environments (TEEs),
zero-knowledge proofs (ZK), re-execution, and formal verification (FV, read
here as formal verification, not the FV homomorphic-encryption scheme).

The short version:

- **No single technique covers the lifecycle.** TEEs are the only one that
  scales to real training and serving, and they rest on trust in hardware
  vendors and on physical security. ZK needs no hardware trust, but today it
  proves inference for GPT-2-sized models at about a hundred tokens per minute
  and cannot prove this project's training run at all. Re-execution is cheap and
  fits training, but needs determinism. FV proves that the protocol and the
  circuits are right; it proves nothing about a particular run.
- **So the architecture is layered.** Every lifecycle step emits a receipt that
  names its parents and states which *evidence tier* backs it. A verifier
  policy decides which tiers it accepts for which step.
- **Multi-cloud is forced, and useful.** No cloud offers everything: AWS has
  cheap multi-node GPUs and attested boot but no confidential GPU, Azure has an
  on-demand single confidential H100, GCP has a second CPU vendor (Intel TDX)
  for the same GPU. Using two TEE vendors is also the only defence against a
  break in one of them.

## Contents

1. [What "verifiable" means: four claims](#1-what-verifiable-means-four-claims)
2. [The techniques and what each one really proves](#2-the-techniques-and-what-each-one-really-proves)
3. [What each cloud offers](#3-what-each-cloud-offers)
4. [The architecture](#4-the-architecture)
5. [The lifecycle, step by step](#5-the-lifecycle-step-by-step)
6. [Where formal verification fits](#6-where-formal-verification-fits)
7. [Limitations nobody can engineer away today](#7-limitations-nobody-can-engineer-away-today)
8. [Gaps in receipt v1 this work exposes](#8-gaps-in-receipt-v1-this-work-exposes)
9. [Build plan mapped to the repo](#9-build-plan-mapped-to-the-repo)
10. [How to choose](#10-how-to-choose)
11. [Sources and confidence](#11-sources-and-confidence)

---

## 1. What "verifiable" means: four claims

People use one word for four different claims. Each technique covers some of
them, and mixing them up is the most common design error.

| Claim | Question it answers | Example |
| --- | --- | --- |
| **Execution integrity** | Did this exact code run on this exact model and input to give this output? | "The answer came from GPT-2 124M with weights `ab12…`, not a cheaper model" |
| **Confidentiality** | Could anyone other than the job see the data or weights? | "The cloud operator could not read my prompts" |
| **Provenance** | Where did each artefact come from? | "These weights came from this training run on this dataset with this code" |
| **Property** | Is something true of the artefact itself? | "Accuracy on the test set is at least 80%"; "the protocol cannot be replayed" |

A second axis is **who the verifier must trust** for the claim to hold. Every
technique moves trust; none removes it.

## 2. The techniques and what each one really proves

### 2.1 Summary

| Technique | Claims it covers | You must trust | Cost over a plain run | Largest workload today | Fails when |
| --- | --- | --- | --- | --- | --- |
| **Signed provenance** (Sigstore, SLSA, hashes, transparency log) | Provenance of builds and artefacts | The build system and the signer's identity provider | About zero | Any | The builder is compromised; says nothing about what ran |
| **Attested boot** (AWS NitroTPM + Attestable AMI, vTPM measured boot) | Integrity of what booted | The cloud's hypervisor and staff | About zero | Any instance type, multi-node, multi-GPU | The cloud itself is the adversary; code loaded after boot is not measured |
| **Confidential VM + GPU** (SEV-SNP or TDX, NVIDIA confidential mode) | Integrity of what booted, confidentiality from the host | AMD or Intel, NVIDIA, their firmware, and the data centre's physical security | Low single digits to about 27% throughput (section 2.2) | One GPU per VM on rentable clouds | Physical access to the server, firmware bugs, side channels |
| **Re-execution** (replication, refereed delegation, spot checks) | Execution integrity | That at least one re-executor is honest, and that the run is deterministic | 1/n of the run per audit, or 2× for full replication | Any, if deterministic | Non-determinism across hardware; verifier must see the data |
| **Zero-knowledge proof** (zkML, zkVM) | Execution integrity, and privacy of weights or inputs *from the verifier* | The proof system's maths and the correctness of the circuit | 10²–10⁵× compute, on a separate prover | Inference at GPT-2 scale practical; billions of parameters claimed; training only at about 10M parameters | Circuit bugs; the proved model is a fixed-point copy, not the float GPU run |
| **Formal verification** | Property (of protocol, code, circuit or small network) | The specification and the proof checker | Engineering time, no runtime cost | Protocols, verifiers, circuit components; networks with thousands to low millions of neurons | The spec misses the attack; hardware is outside the model |

### 2.2 TEEs

**What an attestation says.** "A genuine AMD/Intel CPU, at these firmware
versions, booted a VM whose initial state hashes to M, and a genuine NVIDIA GPU
at these firmware and driver versions is in confidential mode, and both saw
nonce N." Everything beyond that, including "this container produced this
output", is something *you* must build on top by making sure the only code the
VM can run is code that M covers.

**Options.**

| Option | What is measured | Notes |
| --- | --- | --- |
| AMD SEV-SNP confidential VM | Launch measurement of initial firmware | On Azure the measured firmware is Microsoft's paravisor; your OS is measured afterwards into a virtual TPM. So the launch measurement identifies Microsoft's layer, and the OS identity lives in TPM registers (PCRs) |
| Intel TDX confidential VM | Initial firmware (MRTD) plus runtime registers (RTMRs) extended during boot | GCP's confidential H100 VMs use it |
| NVIDIA GPU confidential mode (H100 and later) | GPU firmware, driver, mode | Bound to the VM by an encrypted session set up by the driver inside the VM. The GPU's trust is anchored in the VM's, so a broken CPU TEE breaks the GPU claim too |
| Confidential containers (GCP Confidential Space, Azure confidential containers, the open-source CoCo project) | A locked-down OS that refuses to run anything but one container image, whose digest goes into the token | The cleanest way to bind "this container" to the attestation. Confidential Space on a single H100 is generally available on GCP |
| Process enclaves (Intel SGX) | One process | No GPU. Useful only for small CPU components such as a key broker |
| AWS Nitro Enclaves | One enclave image | No GPU. AWS's answer for GPUs is attested boot (below) |

**Who verifies the evidence.** Either a vendor service (Microsoft Azure
Attestation, Intel Trust Authority, Google Cloud Attestation, NVIDIA NRAS)
turns raw evidence into a signed token, or you verify the raw report yourself
against the vendor's certificate chain. Tokens are simpler and add one useful
fact, that the machine is in that cloud's fleet, at the price of trusting the
service. Raw reports remove the service from the trust base but prove only "a
genuine chip somewhere".

**Limits.**

- **Physical attacks are out of scope by the vendors' own statement.** TEE.fail
  (October 2025) used a DDR5 memory-bus interposer costing under $1,000 to
  extract attestation keys from fully patched TDX and SEV-SNP machines, and
  showed that this also defeats NVIDIA's GPU attestation. AMD and Intel
  responded that physical attacks are outside their threat model. So an
  attestation proves a real chip signed it, not that the chip is unmolested.
  The practical defence is location: accept only evidence that also proves the
  machine is in a hyperscaler's building (cloud-signed tokens), and trust the
  building's guards. On a GPU marketplace or anyone's own hardware, a TEE
  attestation alone is weak evidence of integrity.
- **One GPU per confidential VM in practice.** Hopper (H100) confidential mode
  is single-GPU; its multi-GPU mode assigns all GPUs to one VM and, per one
  source, leaves GPU-to-GPU NVLink traffic unencrypted. Blackwell supports 1,
  2, 4 or 8 GPUs in one confidential VM, but I found no hyperscaler offering
  that on demand. Azure's only confidential GPU size has one H100.
- **No confidential multi-node training.** There is no encrypted RDMA path
  between confidential VMs; all-reduce would run over TLS or IPsec on TCP.
  Confidential training today means one VM.
- **Overhead varies by source.** NVIDIA reports more than 96% of non-confidential
  throughput after tuning; an independent 2026 paper reports 13–27% loss for
  LLM serving under TDX plus GPU confidential mode. Our own number needs the
  non-confidential twin VM, which Azure has not opened to this subscription
  (`docs/external-deps.md` row 20).
- **Large trusted base.** Guest kernel, NVIDIA driver, CUDA, PyTorch or vLLM
  and the model server are all inside. A bug in any is a bug in the claim.
- **Firmware and side channels.** New firmware flaws appear several times a
  year (GCP's release notes list a TDX firmware vulnerability in February 2026).
  A receipt is only as good as the firmware versions its policy accepted *at
  the time*, so verifiers need a revocation list keyed by firmware version.
- **Confidentiality is not secrecy of everything.** Timing, token counts and
  traffic patterns leak.

### 2.3 Attested boot without memory encryption (the AWS route)

AWS sells no instance with a GPU inside an SEV-SNP or TDX boundary. What it
offers since September 2025 is **EC2 instance attestation**: you build an
*Attestable AMI* whose full content has a known measurement, and the instance's
NitroTPM signs an attestation document carrying that measurement and your
nonce. AWS KMS can release a key only to an instance running an approved AMI.
It works on GPU instances, including multi-GPU and multi-node.

It gives the same *integrity* statement as a confidential VM ("this image
booted"), with a different trust root: AWS's Nitro hypervisor, instead of the
CPU vendor. It gives no cryptographic confidentiality from AWS; AWS's position
is that Nitro has no operator access by design. AWS states that part of the
isolation layer (the Nitro Isolation Engine) is formally verified; I did not
confirm which instance families run it.

Use it where the adversary is "our own mistakes and anyone but AWS", which
covers most provenance needs for training.

### 2.4 Zero-knowledge proofs

**What a zkML proof says.** "I know weights W whose commitment is C, and
running circuit F on input x with W gives y." The verifier checks it in
milliseconds to seconds without seeing W (or x, if you hide that instead), and
without trusting any hardware.

**What it does not say.**

- It is not a proof about the GPU run. The prover re-computes the model inside
  an arithmetic circuit over integers. The proved model is a fixed-point
  (quantised) copy, so its outputs can differ from the float model's. To make
  the proof mean something for served traffic, the *served* output has to be
  the circuit's output, or the quantised model has to be the model of record.
- It gives no confidentiality from the prover. The machine generating the proof
  sees weights and inputs in the clear. "Zero-knowledge" protects the prover's
  secrets from the verifier. If the operator must not see user data, ZK does
  not help; that is a TEE's job (or run the prover inside a TEE).
- It does not prove the weights are good, only that committed weights were used.

**State of the art (November 2026), with source quality.**

| System | Approach | Reported result | Source quality |
| --- | --- | --- | --- |
| DeepProve (Lagrange) | Sumcheck/GKR | GPT-2 inference proved at about 174 tokens per minute; verification 1–3.7 s; open source since June 2026 | Authors' paper (ePrint 2026/1112) |
| ZKTorch | Compiler to specialised proof blocks | 6B-parameter GPT-J proof in about 20 minutes on 64 threads | Self-reported |
| zkLLM | Sumcheck with lookup arguments for attention | About 15 minutes for one pass of a 13B model | 2024 paper |
| EZKL and other Halo2 tools | General circuits from ONNX | Practical up to tens of millions of parameters | Multiple sources |
| Independent benchmark of five libraries | - | 6–41 s per inference for a 3.4M-parameter network | Secondary (March 2026) |
| zkVMs (SP1, RISC Zero, Jolt) | Prove any RISC-V program | Orders of magnitude slower than specialised zkML for tensors; right for *small general programs* | Well established |

What this means here: the project's own model is GPT-2 124M, which is exactly
the size the fastest system handles. A 256-token answer would take on the order
of 1.5 minutes to prove against well under a second to generate. That is usable
for audit samples and high-value outputs, not for every request.

**Proof of training.** The best published systems (the CCS 2024 "Kaizen" line,
ePrint 2024/162, and zkDL) prove gradient-descent steps for networks of about
10M parameters, at roughly minutes of proving per iteration. This project's run
is a 124M-parameter transformer for tens of thousands of steps: years of
proving. ZK proof of training is not an option at this scale, and no published
result suggests it will be soon.

**Where ZK does fit in this project.**

1. *Audit-sampled inference proofs* for the 124M model.
2. *Verifiable evaluation*: prove "the committed model scores X on the committed
   test set" without revealing weights (one forward pass per example, no
   generation loop, so much cheaper than serving).
3. *Receipt wrapping in a zkVM*: prove "`alverify` accepted a receipt under
   policy P for output hash H" without revealing the receipt. This hides
   platform details and input hashes from the final verifier and gives one
   small constant-size proof. The program is signature checks and hashing,
   which zkVMs handle in seconds to minutes. It adds no trust over the
   receipt; it only compresses and hides.
4. *Dataset statements*: membership or non-membership of a document in the
   training set, by Merkle proof (no ZK needed unless the set is private).

**Limits beyond scale.** Circuit soundness bugs (a missing constraint lets a
prover prove false statements; see section 6); prover memory in the tens to
hundreds of GB; one proof per generated token unless batched or folded;
sampling randomness must be committed or public; some systems need a trusted
setup; none of the zkML provers has been formally verified.

### 2.5 Re-execution

If a computation is deterministic, anyone can check it by running it again.

| Variant | How it works | Limits |
| --- | --- | --- |
| **Full replication** | Run the job on two independent providers and compare output hashes | 2× cost; both must be bit-identical |
| **Segment spot-check** | The trainer commits to a hash chain of checkpoints; the auditor picks random segments and re-runs checkpoint *k* → *k+1* | Detects cheating with probability proportional to segments checked; catches a single bad step only if that segment is drawn |
| **Refereed delegation** (Verde, Gensyn) | Two providers who disagree bisect the run down to the first differing operation, and a referee re-executes only that one | Needs bit-identical operators across hardware (their RepOps library fixes floating-point order, at a performance cost); needs one honest provider |
| **Activation fingerprints** (TOPLOC) | The server commits to a compact hash of top-k activations; the verifier recomputes with the same model and prompt and compares within a tolerance that absorbs GPU non-determinism | Empirical, not a proof; tested on a few models; verifier must hold the model and see the prompt |
| **Proof-of-learning** (tolerance-based checkpoint checks) | As segment spot-check but accepting small numeric differences | Published spoofing attacks exploit the tolerance |

This project already has the hard prerequisite for the second row: `train.py`
resumes exactly, "a run restarted from a checkpoint produces the same losses as
one that never stopped" (tested on 1 and 2 processes). What is not yet known:
whether that holds bit-for-bit on real L4s with `torch.compile`/AMP settings as
configured, and it will not hold between an L4 and an H100. So audits must
re-run on the same GPU model and the same pinned image.

Re-execution gives no confidentiality: the auditor sees data and weights. The
auditor can itself run inside a TEE.

### 2.6 Signed provenance

Already in the repo for the serving image: Sigstore keyless signing and SLSA
provenance checked offline. The lifecycle needs the same for the training
image, the VM images (so launch measurements can be reproduced by a third
party, otherwise a measurement is an opaque number), datasets (a manifest of
shard hashes) and model files (the existing `files_root_sha256`).
A transparency log (an append-only Merkle log such as Sigstore Rekor, or a
self-run one) adds two things signatures cannot: an ordering in time, and
evidence that the issuer did not show different receipts to different people.

## 3. What each cloud offers

Checked 2026-11-10 to 2026-11-16; rows marked (repo) come from ADR 010 and the
project's own launches.

| | AWS | Azure | GCP |
| --- | --- | --- | --- |
| Confidential GPU VM | None | `Standard_NCC40ads_H100_v5`: SEV-SNP + 1× H100, on-demand, East US 2 and West Europe (also Central US for this subscription) (repo) | `a3-highgpu-1g`: TDX + 1× H100, generally available, three zones; Spot or flex-start only when ADR 006 checked (repo) |
| Multi-GPU confidential | None | None found | None found |
| Attested boot on any GPU instance | Yes: Attestable AMI + NitroTPM, all commercial regions | Trusted launch (vTPM), no first-class "attestable image" flow | Shielded VM (vTPM) |
| Container-level attestation | Nitro Enclaves (CPU only) | Confidential containers (CPU; GPU support not confirmed) | Confidential Space, including single H100 |
| CPU attestation verifier | KMS policy conditions; verify the NitroTPM document yourself | Microsoft Azure Attestation | Google Cloud Attestation or Intel Trust Authority; release notes flag guest-OS versions on which remote attestation on A3 does not work |
| Attestation-gated key release | KMS | Key Vault Managed HSM (secure key release) | KMS with Confidential Space |
| Cheap multi-node GPUs | Yes (L4 `g6`) (repo) | Not used | L4 quota was refused (repo) |
| This project's status | Pool running with 2× g6.xlarge; quota 8 vCPU | Quota granted; one test allocation; non-confidential twin blocked | Removed (ADR 010), code on `gcp-archive` |

Check the three facts that decide the layout yourself:

```sh
# AWS: does any GPU instance type support SEV-SNP? (expect none)
aws ec2 describe-instance-types --region us-east-1 \
  --filters Name=processor-info.supported-features,Values=amd-sev-snp \
  --query 'InstanceTypes[?GpuInfo!=`null`].InstanceType'

# Azure: where is the confidential H100 offered to this subscription?
az vm list-skus --all --size Standard_NCC40ads_H100_v5 -o table

# GCP: is the confidential A3 offered in a zone, and under which provisioning models?
gcloud compute machine-types describe a3-highgpu-1g --zone us-central1-a
```

Beyond the three: GPU marketplaces and smaller GPU clouds increasingly
advertise TDX + NVIDIA confidential mode. Given TEE.fail, their attestations
carry less weight than a hyperscaler's unless you trust their physical
security. On-premises Blackwell is the only route to multi-GPU confidential
training, and is out of scope for a rented project.

## 4. The architecture

### 4.1 Principles

1. **One receipt per lifecycle step, each naming its parents.** The receipts
   form a graph from an inference output back to the dataset.
2. **Every receipt states its evidence tier.** The issuer never upgrades a
   claim; the verifier's policy says which tier each step needs.
3. **Receipts are the only thing clouds share.** No shared identity system, no
   cross-cloud network trust. Artefacts are identified by hash everywhere.
4. **Secrets follow attestation.** Encrypted artefacts; keys released only to a
   workload whose evidence passes the policy.
5. **Two independent mechanisms on anything that matters.** A TEE claim backed
   by a re-execution or ZK sample survives the failure of either.

### 4.2 Evidence tiers

| Tier | Backed by | Trust root | Available for |
| --- | --- | --- | --- |
| **T0 declared** | Signature of the operator | The operator | Everything (it is a log) |
| **T1 attested boot** | NitroTPM / vTPM measurement of a reproducible image | The cloud | Any step on AWS, including multi-node training |
| **T2 confidential** | SEV-SNP or TDX + NVIDIA GPU attestation | CPU vendor, NVIDIA, cloud physical security | Single-GPU steps on Azure or GCP |
| **T3 re-executed** | An independent party reproduced the output hash | One honest re-executor, determinism | Training segments, evaluation, export, deterministic inference |
| **T4 proved** | A ZK proof | Proof system and circuit | Inference and evaluation at 124M parameters; receipt wrapping |

Tiers combine: "T2 + T4 sample" is stronger than either.

### 4.3 Layout

```
                                   TRUST PLANE (runs anywhere, holds no secrets)
        ┌───────────────────────────────────────────────────────────────────────────────────┐
        │ alverify + policy (tiers per step, firmware allow-lists, revocations)             │
        │ transparency log (append-only Merkle log of receipt hashes)                       │
        │ key broker (verifies evidence from any cloud, releases artefact keys)             │
        │ ZK prover box (CPU, big RAM): inference/eval proofs, zkVM receipt wrapping        │
        └───────────────▲───────────────────────────▲───────────────────────────▲───────────┘
                        │ receipts                  │ receipts                  │ receipts
   AWS us-east-1        │       Azure eastus2       │        GCP (optional)     │
  ┌─────────────────────┴───┐  ┌────────────────────┴─────┐  ┌──────────────────┴───────┐
  │ T1 attested boot        │  │ T2 confidential          │  │ T2 confidential          │
  │ Attestable AMI+NitroTPM │  │ SEV-SNP + H100 (1 GPU)   │  │ TDX + H100 (1 GPU)       │
  │                         │  │ MAA + NRAS               │  │ Confidential Space       │
  │ data prep               │  │                          │  │                          │
  │ multi-node training     │  │ confidential inference   │  │ second-vendor inference  │
  │ evaluation, export      │  │ confidential fine-tune / │  │ (cross-check against     │
  │ segment re-execution    │  │  small training / eval   │  │  Azure outputs)          │
  │ S3: datasets, ckpts,    │  │                          │  │                          │
  │  models, receipts       │  │                          │  │                          │
  └─────────────────────────┘  └──────────────────────────┘  └──────────────────────────┘
        encrypted artefacts move between clouds; keys come from the key broker after attestation
```

Why each cloud has its role:

- **AWS** is where multi-node training can run at all, and attested boot is the
  strongest evidence available for it. Re-execution audits run here too,
  because they need the same GPU model as the original run.
- **Azure** is the only on-demand confidential GPU this project has. It serves
  anything that needs confidentiality or the highest hardware-backed tier and
  fits on one H100. The 124M model *does* fit: the whole training run could be
  done inside the confidential VM, giving a T2 training receipt, at the cost of
  no multi-node scaling. Both training routes are worth one run each; the
  comparison is a result in itself.
- **GCP** adds a second CPU vendor. Serving the same request on SEV-SNP and on
  TDX and comparing outputs means one vendor's break does not silently
  invalidate the claim. It is optional and currently blocked by quota.
- **The trust plane** holds no secrets and needs no TEE. The key broker is the
  exception worth hardening (it can run in a small CPU confidential VM).

### 4.4 The receipt graph

```
 dataset manifest ──► training receipt ──► evaluation receipt
 (shard hashes)        │  inputs: dataset root, config, seed, code image
                       │  outputs: checkpoint hash chain, final weights root
                       │  tier: T1 (AWS)  or  T2 (Azure, single GPU)
                       │  + T3 audits: re-executed segments k..k+1
                       ▼
                 export receipt ──► inference receipt ──► (optional) ZK proof
                 (HF format;         model root = export output      T4 over a sample
                  T3: re-run          tier: T2 (Azure / GCP)
                  and compare)
```

A verifier handed an output walks backwards: inference receipt → model root →
export receipt → training receipt → dataset manifest, applying the policy's
tier requirement at each hop, and checks each receipt hash is in the log.

## 5. The lifecycle, step by step

| Step | Runs on | What is proved | How | Limits |
| --- | --- | --- | --- | --- |
| **Data** | AWS | The training set is exactly this list of shard hashes; a given document is or is not in it | `prepare.py` already hashes shards; add a Merkle root and sign it | Says nothing about where the data came from before that, its licence, or whether it is poisoned |
| **Build** | GitHub Actions | Images came from this repo's workflow on `main` | Sigstore + SLSA (exists for the serving image) | Trusts GitHub's runners. VM images must be reproducible for measurements to mean anything |
| **Train (multi-node)** | AWS | The pinned image booted on every node; weights are the end of a committed checkpoint chain; sampled segments reproduce | T1 + T3 | Not confidential. A cheat confined to unsampled segments passes. Bit-exactness on L4 not yet shown |
| **Train (confidential)** | Azure | As above, inside SEV-SNP + GPU confidential mode | T2 (+ T3) | One GPU; the run takes longer than on the pool; re-execution audit needs a second H100 session |
| **Evaluate** | AWS or Azure | The committed model scores X on the committed test set | T3 (cheap, deterministic) or T4 (when the weights must stay private) | A test set that leaked into training makes the score meaningless; nothing detects that cryptographically |
| **Export** | AWS | The serving-format model equals the trained one | Deterministic conversion, re-run and compare hashes | The quantised copy used for ZK is a *different* model and needs its own evaluation receipt |
| **Transfer** | AWS → Azure/GCP | Only an attested workload received the weights | Encrypt in S3; the key broker releases the key against evidence | Today it is `aws s3 sync` and `rsync` by hand (ADR 010) |
| **Serve** | Azure (+ GCP) | This output came from this model and input on an attested confidential VM | T2 receipt (exists for batches) | Per-batch today; see below for per-request |
| **Prove** | Prover box | A sampled output is exactly what the committed model computes | T4 with a zkML prover over the 124M model | Minutes per response; applies to the fixed-point model |
| **Audit and revoke** | Trust plane | A receipt was logged at time t and its platform has not since been revoked | Log inclusion proof; revocation list by firmware version and key | Revocation is a policy decision someone must make and publish |

**Serving, per request.** A fresh attestation per request is too slow (each
needs a round trip to two vendor services). The standard pattern: at boot the
VM creates a key, attests it once (the nonce carries the key's hash, exactly as
ADR 003 does per job), then signs each response with it and periodically logs
a Merkle root of the responses. Clients check one attestation per server
session and one signature per response. This also covers streaming. It weakens
one thing against v1: the attestation no longer covers the output hashes, so a
compromise *after* boot is not visible in it; short key lifetimes bound that.

## 6. Where formal verification fits

FV proves properties of designs and code. It never proves that a particular
job ran correctly.

| Target | Tooling | Effort | What you get | What you do not get |
| --- | --- | --- | --- | --- |
| **The receipt protocol** (nonce binding, key binding, replay, lineage) | Tamarin or ProVerif (symbolic protocol provers) | Days | A machine-checked proof that no attacker who controls the network and old receipts can produce an accepted receipt for content the TEE did not sign, *in the model* | Nothing about the Go code matching the model, or about hardware |
| **The lifecycle rules** (keys released only after attestation; no step accepted below its tier) | TLA+ or Alloy | Days | Exhaustive check of the state machine for small configurations | Same gap to the implementation |
| **The verifier implementation** | Gobra (Go), or rewrite the core in a verifiable language | Weeks to months | Memory safety and functional properties of `alverify` | Rarely worth it; a small, well-tested verifier and a second independent implementation give more per hour |
| **Cryptographic primitives** | Use verified libraries (HACL\*, fiat-crypto, AWS-LC's verified parts) | Near zero | Arithmetic proven correct | Protocol misuse |
| **ZK circuits and zkVMs** | Lean 4 efforts on SP1, Jolt and RISC Zero | Not ours to do | Growing coverage: SP1's core RISC-V chips are being verified (and the work found a bug); Jolt's instruction expansion is verified | No production zkVM is verified end to end, and no zkML prover is verified at all. A missing constraint is the classic failure: the prover can then prove a false statement and nothing looks wrong |
| **TEE isolation code** | Vendor work (AWS states its Nitro Isolation Engine is formally verified; research proofs exist for parts of TDX and Arm's equivalent) | Not ours | Stronger argument for the hypervisor layer | Firmware, microcode and physical attacks stay outside |
| **The neural network** | α,β-CROWN, Marabou | Research-grade | Local robustness bounds for small networks | No meaningful property of a 124M-parameter language model can be stated or proved today |

Recommended for this project: the first two rows, verified crypto libraries,
and nothing else. The Tamarin model is the highest-value FV artefact because
the receipt protocol is the one thing here that is new.

## 7. Limitations nobody can engineer away today

1. **Proving the full training run of a real model cryptographically.** ZK
   cannot; TEEs can only on one GPU; re-execution is probabilistic.
2. **Confidential multi-node training on rented hardware.**
3. **Proof against a physically present attacker.** TEEs do not claim it.
   Only ZK and independent re-execution are immune, and they do not scale.
4. **Proof that the float GPU output is the model's "true" output.** GPU
   arithmetic is not deterministic across hardware; ZK proves a fixed-point
   copy; TEEs prove the software stack, not the arithmetic.
5. **Anything about meaning.** That data is clean, licensed or unpoisoned;
   that a model has no backdoor; that an evaluation is representative. All
   techniques here bind *identities* of artefacts, not their quality.
6. **Vendor honesty.** If AMD, Intel, NVIDIA or the cloud signs a false
   statement, the T1/T2 tiers fail silently. Two vendors reduce this; only T3
   and T4 remove it.
7. **Deletion and unlearning.** Nothing proves data was not kept.
8. **Time.** A receipt's timestamps are the issuer's claim; only the log's
   ordering is evidence.
9. **Long-term validity.** A receipt accepted today may rest on firmware found
   broken next year. Verification results have a date.
10. **Soundness of young proof systems.** zkML provers are new, complex and
    unverified.

## 8. Gaps in receipt v1 this work exposes

Found while reading `receipts/` for this design. Several matter even without
the lifecycle work.

1. **The attestation covers the VM, not the workload.** `alissue` takes
   `--image`, `--model-dir`, `--inputs` and `--outputs` as arguments and hashes
   what it is given. Nothing measured proves that vLLM from that image produced
   those outputs: anyone with a shell in the VM can run `alissue` over files of
   their choice and get a valid receipt. The claim becomes real only when the
   measured boot state covers an image that can run nothing but the serving
   stack and the issuer, with no SSH: an immutable, reproducibly built image
   whose measurement the policy pins, or a confidential-container runtime.
2. **On Azure the launch measurement is Microsoft's firmware layer, not our
   OS.** The policy's `allowed_launch_measurements` therefore cannot identify
   our image; the OS identity is in the virtual TPM's boot registers, which v1
   does not check. (Confirm on the first real token.)
3. **No lineage.** The schema has no parent field, so receipts cannot chain.
4. **`kind: training` exists in the schema, but nothing issues one**, and the
   content model (JSONL inputs and outputs) does not fit a training run.
5. **`platform.cloud` allows only `azure` and `test`;** there is no NitroTPM or
   TDX evidence type with a verifier.
6. **No transparency log and no revocation list.**
7. **No evidence tier**, so a verifier cannot say "training may be T1 but
   serving must be T2".

## 9. Build plan mapped to the repo

Ordered so that each step yields something checkable on a laptop first, as the
project already does. Steps 6.1–6.3 need no cloud spend.

| Step | Change | Where | Depends on |
| --- | --- | --- | --- |
| 6.1 | Receipt v2 schema: `lineage.parents[]`, `evidence.tier`, generic `content` kinds (dataset manifest, checkpoint chain), `platform.cloud` += `aws`, `gcp` | `receipts/schema/`, `receipts/receipt/` | - |
| 6.2 | `alverify --chain`: walk parents, apply per-step tier policy | `receipts/verify/`, `receipts/policy/` | 6.1 |
| 6.3 | Tamarin model of the v2 protocol; TLA+ model of key release | new `receipts/formal/` | 6.1 |
| 6.4 | Dataset manifest + Merkle root in `prepare.py`; checkpoint hash chain in `ckpt.py`; training receipt at job end from the Slurm epilog | `workloads/train/`, `sched/slurm/epilog.sh` | 6.1 |
| 6.5 | Determinism test on real L4s: same segment twice, compare checkpoint hashes; then `make audit-segment JOB=x K=n` | `workloads/train/`, `Makefile` | AWS pool up |
| 7.1 | Attestable AMI for GPU nodes + NitroTPM evidence type and verifier (tier T1) | `infra/gpu-pool/`, `receipts/evidence/`, `receipts/verify/` | AWS pool up |
| 7.2 | Locked-down, reproducible confidential VM image (no SSH, issuer and vLLM only), policy pins its measurement / TPM registers; closes gap 8.1 | `infra/cc-pool/`, `ansible/roles/cc_bringup/` | Phase 5 run on real hardware |
| 7.3 | Per-session attested key + per-response signatures | `receipts/issuer/`, `images/vllm-receipts/` | 7.2 |
| 8.1 | Transparency log and revocation list | new `receipts/log/` | 6.1 |
| 8.2 | Key broker; encrypted model transfer S3 → Azure | new `receipts/broker/`, runbook Phase 5 step 3 | 7.1, 7.2 |
| 9.1 | Quantise the 124M model, evaluate it, prove sampled inferences with a zkML prover; `zk` proof type in the receipt | new `receipts/zk/`, `workloads/` | 6.1 |
| 9.2 | zkVM wrapping of `alverify` | `receipts/zk/` | 6.2 |
| 10 | GCP TDX pool for second-vendor serving | restore from `gcp-archive` | GCP quota |

What exists today, for calibration:

```sh
make test                 # laptop: issue and verify on the test platform, six negative tests
make receipts             # builds bin/alissue, bin/alverify
# Phase 5 on real hardware (runbook), not yet run end to end:
make up POOL=cc
bin/alverify out/receipt.json --policy receipts/policy/prod/policy.yaml --outputs out/outputs.jsonl
```

The single most valuable next step is 7.2. Until the confidential VM image is
locked down and measured, every higher layer inherits gap 8.1.

## 10. How to choose

| If the requirement is | Use | Because |
| --- | --- | --- |
| The operator must not see data or weights | T2 (TEE) | The only technique that gives confidentiality from the operator at GPU speed |
| The verifier trusts no hardware vendor | T4 for inference up to about GPT-2 size; T3 otherwise | ZK and re-execution have no hardware trust root |
| The weights are secret from the *verifier* but the claim must be public | T4 | That is exactly ZK's strength |
| Multi-node training must be accountable | T1 + T3 | Nothing stronger exists for it |
| Hardware is outside a hyperscaler | T3 or T4; treat T2 as supporting evidence | Physical attacks |
| Claims must hold for years | T4 where possible, T3 archives, and dated T2 verification results | TEE firmware ages badly |
| Lowest cost for reasonable assurance | T1/T2 on everything, T3 on a random 1–5% | Sampling deters cheating cheaply |

## 11. Sources and confidence

Confirmed by search on 2026-11-16 (secondary sources unless noted):

- EC2 instance attestation, Attestable AMIs, GPU support: [AWS announcement](https://aws.amazon.com/about-aws/whats-new/2025/09/aws-announces-ec2-instance-attestation), [InfoQ](https://infoq.com/news/2025/10/ec2-instance-attestation)
- GCP confidential H100 (A3, TDX, Confidential Space): [Confidential VM release notes](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/release-notes), [Confidential Space release notes](https://docs.cloud.google.com/confidential-computing/confidential-space/docs/release-notes)
- Azure NCC H100 v5: [size page](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nccadsh100v5-series), [GPU options](https://learn.microsoft.com/en-us/azure/confidential-computing/gpu-options)
- TEE.fail: [The Hacker News](https://thehackernews.com/2025/10/new-teefail-side-channel-attack.html), [SecurityWeek](https://securityweek.com/new-attack-targets-ddr5-memory-to-steal-keys-from-intel-and-amd-tees)
- Multi-GPU confidential mode and overhead: [Super Protocol requirements page](https://superprotocol.com/resources/gpu-cpu-tee-requirements), [Phala paper](https://phala.com/research/papers/2606.23969.pdf), [NVIDIA blog](https://developer.nvidia.com/blog/?p=122756)
- zkML: [DeepProve paper, ePrint 2026/1112](https://eprint.iacr.org/2026/1112), [ZKTorch](https://ddkang.substack.com/p/zktorch-open-sourcing-the-first-universal), [zkML survey](https://arxiv.org/html/2502.18535v2), [verifiable evaluation](https://arxiv.org/pdf/2402.02675.pdf)
- Proof of training: [ePrint 2024/162](https://eprint.iacr.org/2024/162)
- Re-execution: [Verde](https://arxiv.org/html/2502.19405v1), [TOPLOC](https://arxiv.org/html/2501.16007v2)
- Formal verification of zkVMs: [SP1 in Lean](https://blog.succinct.xyz/formal-verification-of-sp1-with-lean/), [Ethereum Foundation on the SP1 bug](https://zkevm.ethereum.foundation/blog/sp1-fv), [Jolt, ePrint 2026/2157](https://eprint.iacr.org/2026/2157), [RISC Zero, Nethermind](https://www.nethermind.io/blog/towards-formal-verification-of-the-first-risc-v-zkvm)

From memory, not re-checked; confirm before relying on them:

- The proof-of-training cost per iteration (section 2.4) and the name "Kaizen".
- Azure's measurement layering (section 8, gap 2).
- The published attacks on proof-of-learning.
- Hopper's NVLink behaviour in multi-GPU mode rests on one vendor-adjacent page.
- Which instance families run AWS's formally verified isolation layer.
- All ZK performance figures are the authors' own; I found no independent
  benchmark that includes the LLM-scale systems.
