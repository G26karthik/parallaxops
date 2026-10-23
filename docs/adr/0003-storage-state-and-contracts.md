# ADR 0003: Object storage, single DB writer, versioned contracts

Status: accepted (Gate 0); S3 server choice re-verified in Gate 2

## Decisions

1. **Blobs live in S3-compatible object storage**, never in Postgres or Temporal history. The API
   uses an S3 adapter (AWS SDK v3) so a real S3/R2/Ceph deployment is a config change.
   *MinIO* was the brief's suggestion, but MinIO no longer distributes Windows binaries (HTTP 410
   from the download host at audit time), so the local default is **SeaweedFS** (Apache-2.0,
   S3 API). The adapter is tested against whichever server is configured.
2. **The API owns the Postgres schema** (Drizzle migrations). The Python worker never connects to
   Postgres: it reports stage/attempt events through an internal, shared-secret endpoint. This
   prevents two-language schema drift and keeps version-skew handling in one place.
3. **Workers need no shared disk.** Inputs and outputs move through object storage by URI and
   checksum; local disk is per-attempt scratch only. Temporal history carries IDs/URIs/checksums.
4. **Versioned contracts.** `manifest.json`, `report.json`, `cameras.json` carry `contract_version`
   (major.minor). Consumers reject unknown majors and tolerate unknown minor fields.
5. **Atomic publication.** Artifacts are assembled in a staging directory/prefix and published by a
   single rename (local) or by writing `manifest.json` last (object store); readers only ever see
   a manifest whose artifacts are all present.
