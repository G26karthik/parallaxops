# ParallaxOps — build state

Resumable status file. Updated after every step. Newest first.

## Decisions locked (see docs/adr/)
- Native-first local stack on Windows; Docker Desktop daemon is off and not required.
- Persistent state lives outside the repo/OneDrive under `$PARALLAXOPS_HOME` (`%LOCALAPPDATA%\ParallaxOps`): venv, tools, datasets, Postgres data, object-store volumes, Temporal DB, scratch.
- Python 3.14 venv, pycolmap 4.2.1 (child process), COLMAP 4.2.1 CUDA CLI for GPU profile + dense.
- No login/signup system (user decision). Tenant isolation stays as a tenant-scoped demo identity (brief acceptance test 4).
- API owns the Postgres schema (Drizzle). Worker reports via an internal endpoint, never writes the schema directly.
- Object store: S3 adapter; MinIO binaries are no longer distributed (HTTP 410), SeaweedFS is the local default.
- Dataset: ETH3D DSLR scenes (ground-truth COLMAP calibration). License text must be verified before any redistribution; downloader only, never committed.

## User instructions to honor
- Plan was approved once by advisor; do NOT call advisor again unless truly blocked (user wants fewer tokens).
- Use latest versions of everything, venv/portable dirs only, no global changes.
- No auth/login system. Tenant isolation via tenant-scoped demo identity only.
- Depth over surface; build it as a real product.
- At ~80% completion run `ponytail:ponytail-audit` (full repo) and apply cuts to reduce LOC.
- No Claude/AI attribution in commits or PRs.

## Gates
| Gate | Status |
|------|--------|
| G0 environment audit | in progress |
| G1 vision core | not started |
| G2 async vertical slice | not started |
| G3 hardening | not started |
| G4 measurement | not started |
| G5 dense / masking / depth | not started |

## Running / queued
- Background: ETH3D `terrace` and `relief` downloads to `$PARALLAXOPS_HOME/data/raw/eth3d`.
- Background: venv + dependency install at `$PARALLAXOPS_HOME/venv`.

## Next step
Verify venv, extract `terrace`, scaffold `services/vision-worker`, write Gate 1 CLI.
