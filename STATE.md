# ParallaxOps — build state

Resumable status file. Updated after every step. Newest first.

## User instructions to honor
- Advisor plan approved once; do NOT call advisor again unless truly blocked (user wants fewer tokens).
- Latest versions of everything; venv / portable dirs only, no global changes.
- No auth/login system. Tenant isolation = tenant-scoped demo identity only (ADR 0004).
- Depth over surface; build it as a real product.
- No Claude/AI attribution in commits or PRs.
- **Never push red.** `python scripts/check.py` = CI definition (pre-push hook runs it; includes actionlint, mypy x3 OS, web build). Reproduce Linux in Ubuntu WSL (`%TEMP%\wsl_full_ci.sh` replica; it clones COMMITTED state, so commit first) before pushing. Fix problems before new work.

## Environment (all under `$PARALLAXOPS_HOME` = `%LOCALAPPDATA%\ParallaxOps`)
- `venv/` Python 3.14.2; `tools/`: node 26.12.1, pnpm 12.15.0, postgres 18.6, seaweedfs 4.49, temporal CLI 1.9.1, actionlint 1.7.12, playwright chromium, colmap-cuda 4.2.1
- Stack: `scripts\dev.ps1 up|down|status|logs` (postgres :5433, s3 :8333, temporal :7233, api :4000, worker :9108, web :3000). Browser extension NOT connected: use Playwright (tests/e2e/test_ui.py).
- Don't kill processes you didn't start; `dev.ps1` uses PID files in `run/`.

## Gates
| Gate | Status |
|------|--------|
| G0 environment audit | done, milestone closed |
| G1 vision core | done, milestone closed |
| G2 async vertical slice | done; evidence in docs/GATES.md; GitHub milestone still open |
| G3 hardening | done (cancel, worker kill, API restart, races, presign tests); Docker Compose files UNVERIFIED; GitHub milestone still open |
| G4 measurement | done; controller v1 did not meet H1 (honest write-up in docs/EXPERIMENTS.md); GitHub milestone still open |
| G5 depth | partly done: dense MVS via COLMAP CUDA works. NOT done: masking comparison, true multi-resolution viewer |

## Ponytail audit (whole repo) — applied
Fixed: job creation atomicity + per-tenant lock, upload race (per-dataset lock), heartbeats no longer stored, N+1 in job lists, dead limits/column/exports/Makefile, doc drift, Python<->TS contract-sync test, engine-failure unit tests.
Reported, NOT done (decide before doing):
- Presigned PUT signs content-type only, not content-length; `complete` re-downloads every byte through the API to hash (needs a SeaweedFS check for signed content-length / x-amz-checksum-sha256).
- Run lists stop at 50 with no cursor; project list unpaged.
- Reconciler and janitor timers run in every API process (safe to repeat, wasteful with replicas).
- No test starts the Temporal workflow with an unknown contract version (docs/VERSIONING.md claims it fails the job).
- Matching switches to sequential above 60 images and assumes names are in capture order; no QC warning yet.
- `pipeline.reconstruct` is still ~250 lines (engine-result reading was extracted and tested).

## Next steps (ordered)
1. Wait for the WSL Linux replica, push, confirm Actions green.
2. Run the 2 real-data integration tests once (`pytest -m integration` in services/vision-worker) to cover the pipeline refactor with real COLMAP.
3. Close GitHub milestones G2..G5 with evidence links (docs/GATES.md anchors).
4. Optional: Docker Compose verification (starting Docker Desktop allowed; stopping it is not), re-measure worker-kill recovery, terminal demo SVG, masking experiment, controller v2 (new pre-registered plan).

## GitHub
- https://github.com/G26karthik/parallaxops (public), milestones G0-G5 (G0, G1 closed).
