# Gate evidence

Each entry lists what was run, what passed, and where the raw output lives. Every number below comes
from a file in `benchmarks/results/` or from a test in this repository. Run the same checks locally with
`python scripts/check.py` (the single definition of CI); live-stack tests are listed per gate.

## Gate 0: environment audit (done)

- `docs/ENVIRONMENT.md` (host, versions, constraints); `docs/adr/0001-0005`.
- Findings that changed the design: Docker daemon off, 6.4 GiB free RAM at audit time, MinIO Windows
  binary gone (HTTP 410, replaced by SeaweedFS), pycolmap PyPI wheel has no CUDA, ETH3D landing page
  unreliable but the data host works.

## Gate 1: vision core (done)

```text
python scripts/check.py                       # ruff, mypy (win32/linux/darwin), pytest (no downloads)
python -m pytest -m integration               # real ETH3D terrace -> real COLMAP -> scored against GT poses
vision reconstruct --input <terrace images> --output runs/terrace --ground-truth <images.txt>
```

Reconstruction (CPU): 23/23 images registered, 5,434 points, 0.845 px mean reprojection, GT camera
RMSE 0.0240 over a 7.65 spread, 72.6 s. Raw files: `benchmarks/results/gate1-terrace/`. Images:
`docs/assets/gate1/`.

| Brief requirement | Test |
|-------------------|------|
| nonempty COLMAP-derived PLY, poses, JSON metrics | `test_real_reconstruction_terrace` |
| typed diagnostic + nonzero exit on bad sets | `test_cli_exit_code_is_typed_on_bad_input`, `test_bad_set_publishes_typed_failure_and_leaves_no_staging` |
| unrelated images fail typed, not crash | `test_noise_images_fail_with_typed_diagnostic_not_crash` |
| engine crash, budget kill, engine-reported failure map to typed diagnostics | `tests/test_engine_result.py` |
| process-tree kill, RSS/disk/time budgets | `tests/test_supervisor.py` |
| path escape / corrupt / oversize input | `tests/test_qc.py` |
| atomic artifact publication | `test_publish_atomic_refuses_clobber_without_force` |
| manifest checksums match files | `test_real_reconstruction_terrace` |

## Gate 2: asynchronous vertical slice (done)

Stack: Postgres, SeaweedFS (S3), Temporal, the Fastify API, the Python worker and the Next.js app, all
native (`scripts/dev.ps1 up`). Tests: `tests/e2e/test_e2e.py`, `tests/e2e/test_ui.py` (Playwright,
including an axe accessibility scan). Raw output: `benchmarks/results/gate2-e2e/`.

- `POST /v1/datasets/{id}/jobs` answers 202 in 45.6 ms; the terrace job then runs QUEUED, PREPROCESSING
  (0.5 s), RECONSTRUCTING (24.8 s), EVALUATING (84.6 s), COMPLETED (87.1 s) (`completed_job.json`).
- A second job queues behind the single heavy slot: it waited 89.6 s and at most one job was active
  (`queueing.json`).
- A failing dataset produces a typed diagnostic with advice, shown in the UI (`failed_job.json`,
  `docs/assets/ui/08-job-failed-diagnostics.png`).
- Artifact links are presigned, tenant-prefixed, and checksums match what the worker recorded; a second
  tenant can neither read the job nor use its links.
- Presigned upload URLs against the real store (`tests/e2e/test_presign.py`): a valid PUT succeeds; a
  tampered signature, a different key and a different content type are all rejected with 403. Expiry
  is not tested (fixed 15-minute TTL).

## Gate 3: hardening (done)

Tests: `tests/e2e/test_resilience.py`, `tests/e2e/test_races.py`, `apps/api/test/*`.

- Cancel: a running job reaches CANCELLED 6.1 s after the request and frees the slot (`cancel.json`).
- Worker killed mid-job: attempt 1 is recorded FAILED, attempt 2 runs on a new worker and the job
  completes, 99.2 s after the kill (`worker_kill_retry.json`; it re-runs the whole job).
- API restarted mid-job: the job still completes, one attempt (`api_restart.json`).
- No scratch left behind after any of the above (`cleanup_check.json`); a killed worker's orphaned
  scratch is swept by the next worker (`tests/test_scratch.py`).
- Idempotency keys, per-tenant queue and storage quotas, tenant isolation, artifact-prefix guard and a
  cleanup janitor have unit tests in `apps/api/test/`. The OpenAPI document is checked for drift.
- Races (found by the repository audit, then fixed): the queue limit holds under a simultaneous
  burst, duplicate upload requests answer `DUPLICATE` instead of 500, and a storage failure while
  creating a job leaves no row behind and consumes no quota. `apps/api/test/races.test.ts` runs on
  PGlite; `tests/e2e/test_races.py` repeats the first two on the real Postgres.
- Python and TypeScript contracts cannot drift silently: `services/vision-worker/tests/test_contract_sync.py`
  compares the diagnostic codes and the config keys.

Not verified: the Docker Compose files in `infra/` have never been executed on this machine.

## Gate 4: measurement (done)

Plan written before any run (`docs/EXPERIMENT_PLAN.md`); results and the honest negative outcome in
`docs/EXPERIMENTS.md`; raw runs in `benchmarks/results/gate4/` (`runs.jsonl`, `runs_posthoc.jsonl`,
`summary.json`). The quality-aware controller did not meet its main hypothesis (H1 not supported);
the overhead hypothesis H2 was supported. API load (`api_load.json`): closed-loop HTTP concurrency with
and without a reconstruction running, no errors at 1, 8 and 32 virtual users; this is not
reconstruction throughput.

## Gate 5: depth (partly done)

- Done: dense MVS through the COLMAP 4.2.1 CUDA binary as an optional stage with a typed fallback.
  Platform run (`benchmarks/results/gate5/dense_job.json`): dense stage 343.9 s, 249,005 points, 678 MiB
  GPU memory; the job completed in 389.7 s. A standalone probe took longer (773 s) for a similar
  cloud; the 2x spread is unexplained. Viewer toggle and screenshots: `docs/assets/ui/09-dense-viewer.png`,
  `10-dense-job.png`. Test: `tests/e2e/test_dense.py`.
- Not done: dynamic-object masking comparison; a true multi-resolution viewer (the viewer decimates to
  200k/300k points).
