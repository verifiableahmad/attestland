# ADR 003: Receipt v1 design choices

- Status: accepted
- Date: 2026-11-07
- Phase / section: Phase 5, spec 7.3–7.6

## Decision

1. **Nonce** = SHA-256(model root ‖ inputs root ‖ outputs root ‖ image digest ‖
   DER SPKI of the signing key), the four hashes as raw 32-byte values. Both
   attestations carry it; the per-job key makes it fresh.
2. **Attest after the job**: the outputs hash is part of the nonce, so the
   issuer hashes the finished batch, makes a key, then attests and signs.
3. **Signature**: ECDSA P-256 over SHA-256 of the RFC 8785 (JCS) body without
   `signature`. The verifier canonicalises the receipt as received, unknown
   fields included, so any edit after signing breaks the signature.
4. **GPU result must be NVIDIA-signed** (NRAS EAT) by default. A local-verifier
   result is unsigned; the policy can allow it only explicitly.
5. **Token expiry is not enforced** on CPU (MAA) and NRAS tokens: receipts are
   checked long after an 8-hour token expires. Freshness comes from the nonce.
6. **Image proof** = the image's SLSA provenance attestation (a Sigstore
   bundle), verified offline with a pinned trusted root, against the expected
   workflow identity, with the in-toto subject equal to the image digest.
7. **Content** v1: canonical JSONL (each line JCS) hashed as one file; the
   model root is SHA-256 over sorted `name:sha256` lines, computed identically
   in Go and Python (tested).

## Consequences

A test platform (MAA-shaped CPU tokens and NRAS-shaped GPU results,
signed by local keys) exercises the whole issue/verify path and all six
negative tests on a laptop. It can never satisfy a
production policy, which pins Microsoft's and NVIDIA's keys.
v1 verifies the MAA token, not the raw SEV-SNP report (ADR 010).
