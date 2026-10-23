# ParallaxOps — build state

Resumable status file. Updated after every step. Newest first.

## User instructions to honor
- Advisor plan approved once; do NOT call advisor again unless truly blocked (user wants fewer tokens).
- Latest versions of everything; venv / portable dirs only, no global changes.
- No auth/login system. Tenant isolation = tenant-scoped demo identity only (ADR 0004).
- Depth over surface; build it as a real product.
- At ~80% completion run `ponytail:ponytail-audit` (full repo) and apply cuts to reduce LOC.
- No Claude/AI attribution in commits or PRs.
- **Never push red.** `python scripts/check.py` = the CI definition (pre-push hook runs it). Reproduce Linux issues in Ubuntu WSL before pushing. Fix problems before continuing new work.

## Environment (all under `$PARALLAXOPS_HOME` = `%LOCALAPPDATA%\ParallaxOps`)
- `venv/` Python 3.14.2 (pycolmap 4.2.1, open3d 0.20, temporalio 1.34, boto3, ruff, mypy, pytest, check-jsonschema)
- `tools/`: node 26.12.1, pnpm 12.15.0 (`tools/pnpm`), postgres 18.6 (`tools/postgres/bin`), seaweedfs 4.49 (`weed.exe`), temporal CLI 1.9.1
- Running services now (started by hand during spikes): Postgres :5433 (`pg/data`, user/db `parallaxops`), SeaweedFS S3 :8333 (`s3/s3.json`), Temporal :7233 (`temporal/temporal.db`)
- Scripts must start services via `Start-Process` (pg_ctl -w hangs with inherited handles)

## Verified spikes (Gate 2 foundations)
- S3 (SeaweedFS): anon denied 403; presigned PUT/GET round trip; tampered key/sig 403; expired 403; CORS only for configured origin; range GET; HEAD
- Temporal dev server: Python SDK works on 3.14; workflow history survives server kill/restart with `--db-filename`
- Postgres 18.6 portable binaries run; no psql.exe shipped (use node `pg`)

## Gates
| Gate | Status |
|------|--------|
| G0 environment audit | done (milestone closed) |
| G1 vision core | done (milestone closed), 20 tests pass incl. real ETH3D integration |
| G2 async vertical slice | IN PROGRESS: infra spikes done; next monorepo (contracts, api), worker activities, web |
| G3 hardening | not started |
| G4 measurement | not started |
| G5 dense / masking / depth | not started |

## GitHub
- https://github.com/G26karthik/parallaxops (public), CI green at 162fa36, milestones G0-G5 created (G0, G1 closed)
- CI: `.github/workflows/ci.yml` runs `scripts/check.py`; apt libs: libusb-1.0-0 libgomp1 libgl1 libegl1

## Design decisions for Gate 2 (do not re-litigate)
- Node 26 + pnpm 12; Next 16, React 19, Fastify 5, Zod 4, Drizzle (pg), Biome (lint+format), vitest, three.
- API owns DB schema; worker reports via internal shared-secret endpoint.
- Tests: PGlite for API unit/integration in CI (no services); real Postgres/S3/Temporal only in local e2e.
- Task queues: `parallaxops-workflows` (workflow), `vision-heavy` (reconstruction, max_concurrent_activities=1), `vision-light` (QC/preflight).
- Temporal payloads: IDs/URIs/checksums only.
- Preflight = separate short workflow running QC on uploaded images.

## Next step
Scaffold pnpm monorepo: packages/contracts, apps/api; install; typecheck under TypeScript 7.
