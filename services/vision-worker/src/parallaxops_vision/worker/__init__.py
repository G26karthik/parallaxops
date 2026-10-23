"""Temporal worker: durable orchestration around the vision pipeline.

Cross-language contract (see packages/contracts/src/workflow.ts): the TypeScript API starts
`ReconstructionWorkflow` / `PreflightWorkflow` with small JSON inputs (ids, object keys, config).
"""

# Major version of the workflow input contract this worker understands. A worker rejects inputs
# with a different major as a non-retryable error, so a rolling upgrade never half-processes a job.
WORKFLOW_CONTRACT = 1

WORKFLOW_NAMES = {"reconstruct": "ReconstructionWorkflow", "preflight": "PreflightWorkflow"}
TASK_QUEUES = {
    "workflows": "parallaxops-workflows",
    "heavy": "vision-heavy",
    "light": "vision-light",
}
