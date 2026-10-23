"""Workflow definitions. Deterministic orchestration only: no I/O, no heavy imports."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

# Plain constants (not imported from the package) keep the sandbox import graph tiny.
WORKFLOW_CONTRACT = 1
HEAVY, LIGHT = "vision-heavy", "vision-light"
MAX_ATTEMPTS = 3
# Workflow-originated terminal events use fixed coordinates so replays are de-duplicated by the API.
TERMINAL_ATTEMPT, TERMINAL_SEQ = 99, 900_000

REPORT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=12,
)


async def _report(
    job_id: str, type_: str, state: str, payload: dict[str, Any], attempt: int = TERMINAL_ATTEMPT
) -> None:
    await workflow.execute_activity(
        "report_event",
        {
            "jobId": job_id,
            "attemptNo": attempt,
            "seq": TERMINAL_SEQ,
            "type": type_,
            "state": state,
            "payload": payload,
        },
        task_queue=LIGHT,
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=REPORT_RETRY,
    )


def _diag_from_error(err: BaseException) -> dict[str, Any]:
    cause: BaseException = err.cause if isinstance(err, ActivityError) and err.cause else err
    code = getattr(cause, "type", None) or "INTERNAL"
    known = {"ENGINE_CRASH", "INTERNAL", "TIMEOUT", "CANCELLED"}
    return {
        "code": code if code in known else "INTERNAL",
        "message": str(cause)[:500] or "Reconstruction failed.",
        "advice": "The reconstruction engine failed repeatedly; see logs or retry with fewer or smaller images.",
        "stage": "engine",
    }


@workflow.defn(name="ReconstructionWorkflow")
class ReconstructionWorkflow:
    @workflow.run
    async def run(self, inp: dict[str, Any]) -> dict[str, Any]:
        job_id = inp["jobId"]
        if inp.get("contract") != WORKFLOW_CONTRACT:
            diag = {
                "code": "INTERNAL",
                "message": f"Worker speaks workflow contract {WORKFLOW_CONTRACT}, got {inp.get('contract')!r}.",
                "advice": "Upgrade the worker or the API so their contract versions match.",
            }
            await _report(job_id, "failed", "FAILED", {"diagnostic": diag})
            raise ApplicationError(diag["message"], non_retryable=True)
        try:
            res: dict[str, Any] = await workflow.execute_activity(
                "run_reconstruction",
                {**inp, "maxAttempts": MAX_ATTEMPTS},
                task_queue=HEAVY,
                # Queue wait is bounded separately from execution time.
                schedule_to_start_timeout=timedelta(hours=6),
                start_to_close_timeout=timedelta(minutes=90),
                # The activity heartbeats every second and the engine is a child process, so the
                # worker's event loop stays responsive. The SDK throttles heartbeats to 80% of this
                # value, which also bounds how long a cancel request takes to reach the activity.
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    maximum_attempts=MAX_ATTEMPTS,
                    non_retryable_error_types=["ContractError"],
                ),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except (asyncio.CancelledError, CancelledError):
            await _report(job_id, "cancelled", "CANCELLED", {})
            raise
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                await _report(job_id, "cancelled", "CANCELLED", {})
                raise
            await _report(job_id, "failed", "FAILED", {"diagnostic": _diag_from_error(err)})
            return {"status": "failed"}

        if res["ok"]:
            await _report(
                job_id,
                "completed",
                "COMPLETED",
                {**res["summary"], "artifacts": res["artifacts"]},
                attempt=res["attemptNo"],
            )
            return {"status": "completed"}
        await _report(
            job_id,
            "failed",
            "FAILED",
            {
                "diagnostic": res["diagnostic"],
                **{k: res["summary"].get(k) for k in ("metrics", "timings", "warnings")},
            },
            attempt=res["attemptNo"],
        )
        return {"status": "failed"}


@workflow.defn(name="PreflightWorkflow")
class PreflightWorkflow:
    @workflow.run
    async def run(self, inp: dict[str, Any]) -> dict[str, Any]:
        res = await workflow.execute_activity(
            "run_preflight",
            inp,
            task_queue=LIGHT,
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(
                maximum_attempts=2, non_retryable_error_types=["ContractError"]
            ),
        )
        body = {"datasetId": inp["datasetId"], **res}
        await workflow.execute_activity(
            "report_preflight",
            body,
            task_queue=LIGHT,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REPORT_RETRY,
        )
        return {"ok": res["ok"]}
