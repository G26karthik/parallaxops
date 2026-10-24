# External review: triage

An outside reviewer audited `main` at `f1051be` and sent a written report. This page records what
was done with each finding. A finding is **fixed** (with the commit's test), **documented** (the
claim in the docs was corrected), or **deferred** (real, not done yet, kept in the backlog below).
Nothing was dismissed without a reason. The report was treated as input to verify, not as a list of
orders: each point was checked against the code first.

| Finding | Verdict | What changed |
|---------|---------|--------------|
| Tenant id is scoping, not authentication | Accepted, by design | Already ADR 0004 and README "Limitations". No login system is a standing requirement of this project; the API must not be exposed to the internet as-is. |
| `getTenantId` returned a new id on every call when browser storage was blocked, so each component saw its own empty tenant | **Fixed** | One id per page load in that case. Test written first, failed, then passed: `apps/web/test/tenant.test.ts`. |
| Production config refused only the default internal secret; the S3 access and secret keys kept their development defaults | **Fixed** | `loadConfig` in production now refuses all three. Test: `apps/api/test/config.test.ts`. |
| `scripts/check.py` silently skipped every TypeScript step when `pnpm` was missing | **Fixed** | A workspace without pnpm now fails the run with a message instead of a shorter, green check. |
| CI does not run the live stack, so a worker/API vocabulary drift can pass CI | **Partly fixed** | The worker's recorded event stream (`packages/contracts/fixtures/worker_events.json`) is checked by a Python test and replayed through the real API in `apps/api/test/worker_events.test.ts`. Mutation-checked: reverting the phase-heartbeat fix fails it. Terminal events built by the Temporal workflow are not in that file. A CI job that starts Postgres, Temporal and the object store stays in the backlog. |
| Controller v1 prefers registered fraction and reprojection error, which can favour wrong geometry | Accepted, documented | This is the main finding of the controller experiment (`docs/EXPERIMENTS.md`). README now also says the controller is experimental and not wired into the platform: jobs always run the fixed baseline configuration. |
| Multi-host scaling is a design: one heavy slot per worker, no global GPU lease | Accepted, documented | Already stated in `docs/ARCHITECTURE.md` and README. Not built: a distributed lease and a multi-worker run. |
| Docs were stale against the head commit | **Fixed** | Gate evidence, test counts, dense numbers and image captions were rewritten from current code and measured runs. |
| PXLD "keeps every point" is qualified by the 8 M encoder cap and the 3 M viewer budget | **Fixed (wording)** | `docs/ARCHITECTURE.md`, `docs/GATES.md` and README state the limits. |
| Presigned PUT does not sign the content length, and `complete` re-reads whole objects | **Fixed** | The URL now signs the declared size and the SHA-256 (`x-amz-checksum-sha256`). Checked against the real SeaweedFS in `tests/e2e/test_presign.py`: a PUT one byte larger or smaller gets 403, same size with different bytes gets 400 `BadDigest`, a dropped digest header gets 403. SeaweedFS keeps the verified digest, so `complete` reads it with a `HEAD` instead of streaming every object back through the API; a store that keeps none falls back to the old re-read. Unit tests: `apps/api/test/storage.test.ts`, `uploads.test.ts`. |
| List endpoints are not paged | **Fixed** | Projects, datasets and jobs take `limit` (1 to 200) and `cursor`; the next cursor comes back in the `x-next-cursor` header, so the response bodies did not change. Default 200 for projects and datasets, 50 for jobs. Newest first with the id as tie-break, no gaps or repeats, tenant-scoped (`apps/api/test/paging.test.ts`). The web app does not page yet and shows the newest 200 projects. |
| Janitor and reconciler timers run in every API replica | **Fixed** | Each timer first takes a lease row in Postgres (`maintenance_leases`, one atomic upsert); only the holder does the work, and another replica takes over when the lease expires (`apps/api/test/lease.test.ts`). Tested with two simulated holders on one database, not with two real API processes. |
| Shuffled filenames silently break sequential matching above 60 images (found in our own audit) | **Fixed** | QC compares how alike name-adjacent images are with how alike random pairs are, and warns when the ratio is above 0.9. Calibrated on two real sets (ordered 0.54 and 0.83, the same sets shuffled 0.93 to 1.08); only two sets, so the threshold is a measured guess, not a proof. `services/vision-worker/tests/test_capture_order.py`. |
| No test starts the Temporal workflow with an unknown contract version (found in our own audit) | **Fixed** | `services/vision-worker/tests/test_workflow_skew.py` runs the real workflow on a throwaway dev server with stub activities. Mutation-checked: disabling the guard fails it. It also pins the `completed` and `failed` bodies the workflow sends. Needs the Temporal CLI, so it is skipped in CI. |
| Docker Compose files never executed | Deferred, stated | Listed under "Not verified" in `docs/GATES.md`. |
| Observability and benchmark coverage are limited | Accepted | Two ETH3D variants of one scene and one TUM sequence; stated in README "Limitations". |
| Dataset license diligence (ETH3D, TUM) | Documented | ETH3D and TUM are both marked unconfirmed in `docs/DATASETS.md` (TUM section added); no TUM imagery is committed. |

## Backlog from this review

1. A CI job that runs the live stack (Postgres, object store, Temporal, worker) for the e2e suite.
   The workflow-level tests (`test_workflow_skew.py`) and the live e2e suite need local tools, so
   CI still cannot see them.
2. A fleet-wide heavy-job lease (the new `maintenance_leases` table is the pattern to follow).
3. Run the Docker Compose path once and record the result.
4. Paging in the web app, and a real two-process test of the lease.
5. A Prometheus scrape test and more benchmark scenes.
