# ParallaxOps — build state

Resumable status file. Updated after every step. Newest first.

## User instructions to honor
- Advisor plan approved once; do NOT call advisor again unless truly blocked (user wants fewer tokens).
- Latest versions of everything; venv / portable dirs only, no global changes.
- No auth/login system. Tenant isolation = tenant-scoped demo identity only (ADR 0004).
- Depth over surface; build it as a real product.
- At ~80% completion run `ponytail:ponytail-audit` (full repo) and apply cuts to reduce LOC.
- README style inspiration: ruvnet/ruflo (hero banner, badge row, SVG icons, animated terminal demo). Style only, not text.
- No Claude/AI attribution in commits or PRs.
- **Never push red.** `python scripts/check.py` = CI definition (pre-push hook runs it; includes actionlint, mypy x3 OS, web build). Reproduce Linux in Ubuntu WSL (`%TEMP%\wsl_full_ci.sh` replica) before pushing CI-affecting changes. Fix problems before new work.

## Environment (all under `$PARALLAXOPS_HOME` = `%LOCALAPPDATA%\ParallaxOps`)
- `venv/` Python 3.14.2; `tools/`: node 26.12.1, pnpm 12.15.0, postgres 18.6, seaweedfs 4.49, temporal CLI 1.9.1, actionlint 1.7.12, playwright chromium
- Stack: `scripts\dev.ps1 up|down|status|logs` (postgres :5433, s3 :8333, temporal :7233, api :4000, worker :9108, web :3000). Browser extension NOT connected: use Playwright (tests/e2e/test_ui.py).
- Don't kill processes you didn't start; `dev.ps1` uses PID files in `run/`.

## Gates
| Gate | Status |
|------|--------|
| G0 environment audit | done, milestone closed |
| G1 vision core | done, milestone closed |
| G2 async vertical slice | DONE in code: API+Temporal worker+S3+Postgres+Next.js; real e2e (API + browser) pass; pushed at 036c1f8 CI green. Milestone G2 to close with README evidence |
| G3 hardening | mostly done: idempotency, cancel, quotas, tenancy tests, orphan-attempt handling, artifact prefix guards, janitor, worker-kill e2e (passed), API-restart e2e (running/pending), OpenAPI drift test. TODO: rerun/confirm resilience results committed; Docker compose (unverified label) |
| G4 measurement | IN PROGRESS: plan committed (d08afc3), controller + tests done, runner/report written. NEXT: generate variants, run terrace (3 reps) + relief (1 rep) in background, report, API load test (benchmarks/api_load.py) |
| G5 depth | not started (dense MVS via COLMAP CUDA CLI, masking comparison in separate venv, multi-res viewer) |

## Next steps (ordered)
1. Confirm `tests/e2e/test_resilience.py` finished (log `logs/resilience.out.log`); commit evidence in `benchmarks/results/gate2-e2e/`.
2. `python benchmarks/variants.py terrace`; `python benchmarks/run_experiment.py --scene terrace --reps 3 --budget 100` (background, ~1.5 h), then relief (`--reps 1 --budget 120 --max-image-size 1200`); `python benchmarks/report.py`; write `docs/EXPERIMENTS.md` honestly.
3. API load test under bounded concurrent clients while idle and while a reconstruction runs (state conditions).
4. README redesign (hero SVG, badges, screenshots from docs/assets/ui, measured tables), `docs/` index, security notes, API examples.
5. Gate 5: COLMAP CUDA 4.2.1 binary probe, dense MVS bounded; masking experiment (separate venv).
6. ponytail audit at ~80%, apply cuts, rerun full checks.
7. Close GitHub milestones G2..G5 with evidence links; final handoff.

## GitHub
- https://github.com/G26karthik/parallaxops (public), milestones G0-G5 (G0, G1 closed), CI green at 036c1f8.
