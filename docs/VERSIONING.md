# Versioning and version skew

The API and the worker deploy independently, so every boundary carries a version.

| Boundary | Version | Where |
|----------|---------|-------|
| Workflow input and input manifest | integer `contract` (currently `1`) | `packages/contracts/src/workflow.ts`, `worker/__init__.py` |
| Artifact JSON (`manifest.json`, `report.json`, `cameras.json`) | `contract_version` `major.minor` | `parallaxops_vision/__init__.py` |
| HTTP API | `/v1` path prefix; OpenAPI in `docs/openapi.json` | generated, drift-tested |
| Reconstruction config | `schema_version` | `parallaxops_vision/config.py` |

## Rules

- **Additive changes** (new optional field) bump the *minor* version. Consumers ignore unknown
  fields.
- **Breaking changes** bump the *major*. A component that receives an unknown major **rejects it
  explicitly**. It never half-processes it.

## Behaviour under skew (implemented)

| Situation | What happens |
|-----------|--------------|
| API sends workflow `contract: 2` to a worker that speaks 1 | The workflow reports a `FAILED` job with the message "Worker speaks workflow contract 1, got 2" and the advice to upgrade; the activity raises a non-retryable `ContractError`. The job does not hang. |
| Manifest from a different dataset or tenant than the workflow input | Rejected as `ContractError` (non-retryable) before any image is downloaded. |
| Worker reports an artifact key outside the job's tenant prefix | The API drops that artifact. |
| New optional response fields | The browser's generated types ignore them until regenerated. |

## Rolling upgrade order

1. Deploy the worker that understands both majors.
2. Deploy the API that starts emitting the new major.
3. Retire the old major in the worker in a later release.

Tests: `tests/test_workflow_skew.py` (the real workflow on a throwaway Temporal dev server: an unknown
contract fails the job without running the engine, and completed and failed runs are reported with
their summary; needs the Temporal CLI, so it does not run in CI),
`tests/test_worker.py` (contract and manifest guards), `apps/api/test/openapi.test.ts`
(spec drift), `packages/contracts/test` (state machine and limits).
