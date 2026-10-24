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
| G2 async vertical slice | done; evidence in docs/GATES.md; milestone closed |
| G3 hardening | done (cancel, worker kill, API restart, races, presign tests); Docker Compose files UNVERIFIED; milestone closed |
| G4 measurement | done; controller v1 did not meet H1 (honest write-up in docs/EXPERIMENTS.md); milestone closed |
| G5 depth | in progress: dense MVS via COLMAP CUDA works; multi-resolution viewer built and verified live on a sparse job (dense not re-run); masking experiment DONE (null result: no accuracy change, segmentation cost 493 s, see docs/EXPERIMENT_MASKING.md) |

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
CI green at f1051be. Milestones G2-G4 closed with evidence links; G5 stays open.
Regression found and fixed (bd011f9): the audit commits 0a082f7..2d98244 left real jobs stuck in
PREPROCESSING (phase changes arrive as state-carrying heartbeats). CI cannot see this; run the live
e2e (`tests/e2e/test_ui.py`) after any change to event handling.
1. Masking experiment finished (results in docs/EXPERIMENT_MASKING.md). Optional follow-up: a harder
   sequence (e.g. TUM walking_halfsphere) since walking_xyz left the baseline at the ceiling.
2. Re-run `tests/e2e/test_dense.py` (GPU, about 7 minutes) and measure the viewer on the dense cloud.
3. Audit items listed above that were reported but not done.
3. Optional: Docker Compose verification (starting Docker Desktop allowed; stopping it is not), re-measure worker-kill recovery, terminal demo SVG, masking experiment, controller v2 (new pre-registered plan).

## GitHub
- https://github.com/G26karthik/parallaxops (public), milestones G0-G5 (G0-G4 closed, G5 open).
