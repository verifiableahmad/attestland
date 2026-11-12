# ADR 008: Cloud Storage for the durable tier, replacing MinIO

- Status: superseded by ADR 010 (the durable tier is S3, reached with the nodes' instance role)
- Date: 2026-11-08
- Phase / section: Phase 4, spec 5.1 (supersedes ADR 002)

## Decision

The durable copy of checkpoints, datasets, model weights and receipts lives in four
versioned Google Cloud Storage buckets (`<prefix>-datasets`, `-checkpoints`, `-models`,
`-receipts`; prefix defaults to the project id), created by `infra/gpu-pool/storage.tf`.
The storage VM, the MinIO role and the MinIO source-build Dockerfile are removed.

Code keeps talking the S3 API. It uses Cloud Storage's S3 interoperability endpoint
(`https://storage.googleapis.com`) with an HMAC key for the nodes' service account
(`AL_S3_ENDPOINT`, `AL_S3_ACCESS_KEY`, `AL_S3_SECRET_KEY`, `AL_BUCKET_PREFIX`).
The training scripts source `/etc/fleet/s3.env`; the vLLM init container reads a
`s3-creds` Kubernetes secret. Checkpoint modes were renamed `sync_durable` and
`direct_durable`, and the restore tier `durable`, since the backend is no longer one product.

## Context

MinIO's community edition stopped shipping images in October 2025, went into maintenance
mode in December 2025 and was later archived (secondary sources disagree on the date;
check the repository). ADR 002 built a pinned tag from source, which still compiles but
will get no security fixes. Everything else already runs on GCP (ADR 006).

## Options considered

| Option | For | Against |
| --- | --- | --- |
| Cloud Storage via S3 interoperability (chosen) | Nothing to run or patch; one fewer VM; versioning and lifecycle rules built in; code barely changes | HMAC keys are long-lived secrets (they sit in tofu state and the inventory); the interoperability API is a subset of S3 |
| Cloud Storage with the native client | No HMAC key; Workload Identity | Rewrites the checkpoint code and the DCP storage writer |
| Community MinIO fork (pgsty/minio) | Same software, images available | One maintainer group to trust; still a server to run |
| Garage or SeaweedFS | Maintained, S3 API | A server to run and benchmark; different behaviour |

## Consequences

- Checkpoint tests now run against moto's S3 server on a laptop (6 tests, always run).
  They prove the code, not Google's endpoint.
- Recent boto3 sends CRC checksum headers that Google's endpoint can reject, so every
  client sets `request_checksum_calculation=when_required` (boto3, s3fs, aws-cli env vars).
  This was an assumption until the first run.
- Durable-tier timings change: Cloud Storage over the network instead of MinIO on local SSD.
  The Young–Daly interval uses the measured upload time, so Phase 4 numbers must be re-taken.
- Old object versions are deleted 14 days after they stop being current. `tofu destroy`
  refuses to delete non-empty buckets unless `bucket_force_destroy = true`.
- The `minio` Prometheus job is gone. Cloud Storage metrics come from Cloud Monitoring.
- Revisit if the HMAC key is a problem: switch to the native client and Workload Identity.
