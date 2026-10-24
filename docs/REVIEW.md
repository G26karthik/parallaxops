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
| Presigned PUT does not sign the content length, and `complete` re-reads whole objects | Deferred | Needs a signed size on upload and a streamed checksum. Today `complete` rejects an object whose stored size differs from the declared size, but only after the bytes are stored, so an oversized PUT still lands in the bucket until cleanup. |
| List endpoints are not paged | Deferred | Cursor pagination on projects, datasets, jobs. |
| Janitor and reconciler timers run in every API replica | Deferred | Not verified under concurrent replicas; the fix is a single owner (advisory lock or one scheduler). |
| Docker Compose files never executed | Deferred, stated | Listed under "Not verified" in `docs/GATES.md`. |
| Observability and benchmark coverage are limited | Accepted | Two ETH3D variants of one scene and one TUM sequence; stated in README "Limitations". |
| Dataset license diligence (ETH3D, TUM) | Documented | ETH3D and TUM are both marked unconfirmed in `docs/DATASETS.md` (TUM section added); no TUM imagery is committed. |

## Backlog from this review

1. Sign the upload size in presigned PUTs and verify the checksum while streaming.
2. Cursor pagination for list endpoints.
3. One owner for the janitor and reconciler across replicas.
4. A CI job that runs the live stack (Postgres, object store, Temporal, worker) for the e2e suite.
5. A fleet-wide heavy-job lease.
6. Run the Docker Compose path once and record the result.
7. A Prometheus scrape test and more benchmark scenes.
