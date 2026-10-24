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
- `check.py` runs `next build`, which breaks a running `next start`: `dev.ps1 restart web` afterwards (docs/CI.md). `dev.ps1 restart api` after API changes (node does not hot reload).

## Gates
| Gate | Status |
|------|--------|
| G0 environment audit | done, milestone closed |
| G1 vision core | done, milestone closed |
| G2 async vertical slice | done; evidence in docs/GATES.md; milestone closed |
| G3 hardening | done (cancel, worker kill, API restart, races, presign tests); Docker Compose files UNVERIFIED; milestone closed |
| G4 measurement | done; controller v1 did not meet H1 (honest write-up in docs/EXPERIMENTS.md); milestone closed |
| G5 depth | done: dense MVS via COLMAP CUDA; multi-resolution viewer verified live on sparse and dense jobs (dense job 413 s, 248,468 points, viewer numbers in docs/PERFORMANCE.md); masking experiment (null result: no accuracy change, segmentation cost 493 s, see docs/EXPERIMENT_MASKING.md) |

## Ponytail audit (whole repo) — applied
Fixed: job creation atomicity + per-tenant lock, upload race (per-dataset lock), heartbeats no longer stored, N+1 in job lists, dead limits/column/exports/Makefile, doc drift, Python<->TS contract-sync test, engine-failure unit tests.
External review (docs/REVIEW.md) fixed: tenant-id fallback, production refusal of default S3 keys, check.py no longer skips TS steps without pnpm, worker/API event replay test, signed upload size and digest (SeaweedFS verifies it; `complete` no longer re-reads), cursor paging, lease-guarded janitor/reconciler, QC warning for shuffled filenames. The rest is the backlog there.
Reported, NOT done (decide before doing):
- No CI job runs the live stack; no fleet-wide GPU lease; Docker Compose never run.
- `pipeline.reconstruct` is still ~250 lines (engine-result reading was extracted and tested).

## Next steps (ordered)
CI green at f1051be; later commits are pushed only after `check.py` and the WSL replica pass.
Regression found and fixed (bd011f9): the audit commits 0a082f7..2d98244 left real jobs stuck in
PREPROCESSING (phase changes arrive as state-carrying heartbeats). CI cannot see live-stack
regressions; `tests/e2e/test_ui.py` and `test_dense.py` do. Run them after any change to event
handling. The recorded worker events are now also replayed through the API in unit tests.
1. Optional: masking on a harder sequence (TUM walking_halfsphere), since walking_xyz left the
   baseline at the ceiling.
2. Audit items listed above that were reported but not done, and the backlog in docs/REVIEW.md.
3. Optional: Docker Compose verification (starting Docker Desktop allowed; stopping it is not), re-measure worker-kill recovery, terminal demo SVG, controller v2 (new pre-registered plan).

## GitHub
- https://github.com/G26karthik/parallaxops (public), milestones G0-G5 (G0-G4 closed, G5 open).
