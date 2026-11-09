# ADR 002: Build MinIO from source, use aws-cli instead of mc

- Status: superseded by ADR 008 (MinIO's community edition is archived; the durable tier is Cloud Storage)
- Date: 2026-11-07
- Phase / section: Phase 4, spec 5.1

## Decision

The durable tier stays MinIO, built from the upstream source tag
(`storage/minio/Dockerfile`, `MINIO_VERSION` in versions.env). Bucket setup and
weight downloads use the AWS CLI image and boto3 instead of MinIO's `mc`.

## Context

On 2026-11-07, `minio/minio` and `quay.io/minio/minio` could not be pulled
(Docker Hub: repository not found; quay: 401), and the same holds for `mc`.
Upstream publishes source releases only (latest tag RELEASE.2025-10-15T17-29-55Z).
The spec's pinned image therefore no longer exists.

## Options considered

| Option | For | Against |
| --- | --- | --- |
| **Build MinIO from source at a pinned tag (chosen)** | Same software as the spec; pinned; reproducible | We own the image build; security fixes need a rebuild |
| Chainguard or other third-party MinIO image | No build | Free tier is `latest` only (cannot pin); third-party trust |
| Switch to SeaweedFS / Garage / Ceph RGW | Maintained images | Different behaviour and benchmarks; spec says MinIO |

## Consequences

The image build runs on the storage VM during `make up` (~3 min). Tested
locally: the checkpoint code (save, async upload, restore from MinIO, disk-full
fallback) passes against the source-built image. Revisit if MinIO stops
publishing source tags, then move to SeaweedFS.
