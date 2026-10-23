# Security and privacy

ParallaxOps is a **local-first** system. It has no login system by design (ADR 0004). This page
states what is enforced, what is tested, and what is *not* protected.

## Threat model in one paragraph

A single user (or a few) runs the stack on a trusted machine. The control plane must still be
robust against malformed or hostile input (uploads, ids, headers), must never mix one tenant's
data with another's, must not let the computation plane be abused to read arbitrary files, and must
not leak secrets or host details. It does **not** defend against someone who can already run code on
the host or read the data directory.

## Enforced and tested

| Area | Control | Test |
|------|---------|------|
| Tenant isolation | Every row carries `tenant_id`; every query is scoped; the tenant comes only from `X-Tenant-Id`, never from a body or query string; cross-tenant ids return 404 (no existence oracle) | `apps/api/test/tenancy.test.ts` |
| Object keys | `tenants/<tenant>/...` prefixes; user filenames never appear in keys (only generated ids); the API refuses to presign, and drops artifacts, outside the caller's prefix | `tenancy.test.ts`, `jobs.test.ts` |
| Worker trust | The worker uses a shared secret for the internal endpoints (constant-time compare); the browser's tenant header never reaches them; the worker rejects a manifest whose keys leave the job's tenant/dataset prefix or contain `..` | `tenancy.test.ts`, `tests/test_worker.py` |
| Uploads | Allow-list of types; size and count caps; `Content-Type` signed into the presigned URL; server-side magic-byte check and **full SHA-256 verification** after upload; mismatches are deleted; filenames sanitized (path separators, control characters, bidi characters) | `uploads.test.ts`, `contracts.test.ts` |
| Presigned URLs | Short-lived (15 min), bound to one key and method; tampered signature, other key, or expiry rejected by the store | spike in `docs/GATES.md` (S3 section) |
| CORS | Only the configured web origins on the API and on the bucket | S3 spike, `apps/api/src/app.ts` |
| Process isolation | The SfM engine runs as `python -I` in a child process with argv arrays only (no shell, never `COLMAP.bat`), wall-clock/RSS/disk budgets, and kill-on-close Job Object | `tests/test_supervisor.py`, `test_resilience.py` |
| Path traversal in inputs | Input listing never follows links out of the input tree; per-job scratch dirs are created by id, not from user data | `tests/test_qc.py` |
| Error hygiene | Errors carry a request id and a code, never a stack trace or file path; request ids from clients are validated | `tenancy.test.ts` |
| Secrets | No secrets in the repo; `.env.example` holds local-only development values; the API refuses to boot in production with the default internal secret | `apps/api/src/config.ts` |
| Supply chain | `pnpm install --frozen-lockfile`; lifecycle scripts blocked except an allow-list (`esbuild`) | `pnpm-workspace.yaml` |

## Not protected (be honest about it)

- **Tenant ids are not secrets.** Anyone who learns another browser's tenant id can act as that
  tenant. Scoping prevents accidental mixing and enables the production path (a verified token
  mapped to the tenant), but it is not authentication.
- The API listens on `127.0.0.1` by default. There is no TLS and no rate limiting beyond per-tenant
  quotas. Put it behind a reverse proxy with TLS and real authentication before exposing it.
- Local development credentials for the object store are in `.env.example` and are not secret.
- Presigned URLs are bearer tokens for their 15-minute lifetime.
- Uploaded images are decoded by Pillow/OpenCV/COLMAP. Decoder vulnerabilities are inherited; the
  pixel budget and the process isolation limit the blast radius but do not remove it.

## Privacy

Photographs never leave the machine. No cloud service, analytics, telemetry or third-party API
is used. The optional LLM explanation feature in the brief is **not implemented**; if added it
must be opt-in, receive only already-computed metrics (never images), and have a deterministic
fallback.

## Datasets

ETH3D imagery is treated as **CC BY-NC-SA 4.0 (unconfirmed)** and is never committed or
redistributed (see `docs/DATASETS.md`). Screenshots in this repository show only point-cloud
colours and downscaled renders derived from it, for research demonstration, with attribution.
