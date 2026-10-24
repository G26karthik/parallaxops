"""The real ReconstructionWorkflow on a throwaway Temporal dev server, with stub activities.

Needs the Temporal CLI (`$PARALLAXOPS_HOME/tools/temporal` or `temporal` on PATH); skipped without
it, so it does not run in CI. It pins what docs/VERSIONING.md promises about version skew and what
the workflow reports to the API at the end of a job.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from parallaxops_vision.worker import TASK_QUEUES
from parallaxops_vision.worker.workflows import WORKFLOW_CONTRACT, ReconstructionWorkflow


def find_temporal() -> str | None:
    home = os.environ.get("PARALLAXOPS_HOME") or (
        str(Path(os.environ["LOCALAPPDATA"]) / "ParallaxOps")
        if "LOCALAPPDATA" in os.environ
        else ""
    )
    for name in ("temporal.exe", "temporal"):
        p = Path(home) / "tools" / "temporal" / name
        if home and p.exists():
            return str(p)
    return shutil.which("temporal")


TEMPORAL = find_temporal()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(TEMPORAL is None, reason="Temporal CLI not installed"),
]

JOB = str(uuid.uuid4())


async def run_workflow(
    contract: int, heavy_result: dict[str, Any]
) -> tuple[Any, list[dict[str, Any]], int]:
    """Runs the workflow; returns (outcome or error, bodies sent to report_event, heavy calls)."""
    reported: list[dict[str, Any]] = []
    heavy_calls = 0

    @activity.defn(name="report_event")
    async def report_event(body: dict[str, Any]) -> dict[str, Any]:
        reported.append(body)
        return {"applied": True}

    @activity.defn(name="run_reconstruction")
    async def run_reconstruction(_inp: dict[str, Any]) -> dict[str, Any]:
        nonlocal heavy_calls
        heavy_calls += 1
        return heavy_result

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL) as env:
        client: Client = env.client
        async with (
            Worker(client, task_queue=TASK_QUEUES["workflows"], workflows=[ReconstructionWorkflow]),
            Worker(client, task_queue=TASK_QUEUES["light"], activities=[report_event]),
            Worker(client, task_queue=TASK_QUEUES["heavy"], activities=[run_reconstruction]),
        ):
            try:
                out: Any = await client.execute_workflow(
                    "ReconstructionWorkflow",
                    {"jobId": JOB, "contract": contract, "config": {}},
                    id=f"reconstruct-{JOB}",
                    task_queue=TASK_QUEUES["workflows"],
                    # a broken workflow fails its tasks and retries forever: end it, then assert
                    execution_timeout=timedelta(seconds=20),
                )
            except WorkflowFailureError as err:
                out = err
    return out, reported, heavy_calls


def test_unknown_contract_fails_the_job_without_running_the_engine() -> None:
    out, reported, heavy = asyncio.run(run_workflow(WORKFLOW_CONTRACT + 1, {}))
    assert isinstance(out, WorkflowFailureError)
    assert heavy == 0
    assert len(reported) == 1
    body = reported[0]
    assert (body["type"], body["state"]) == ("failed", "FAILED")
    assert (
        f"contract {WORKFLOW_CONTRACT}, got {WORKFLOW_CONTRACT + 1}"
        in body["payload"]["diagnostic"]["message"]
    )
    assert body["payload"]["diagnostic"]["advice"]


def test_a_finished_run_is_reported_as_completed_with_its_summary_and_artifacts() -> None:
    artifacts = [
        {"kind": "report", "key": "k", "bytes": 1, "sha256": "0" * 64, "contentType": "x/y"}
    ]
    result = {
        "ok": True,
        "attemptNo": 2,
        "summary": {"metrics": {"n_points3d": 7}, "quality": {"level": "good"}},
        "artifacts": artifacts,
    }
    out, reported, heavy = asyncio.run(run_workflow(WORKFLOW_CONTRACT, result))
    assert out == {"status": "completed"} and heavy == 1
    (body,) = reported
    assert (body["type"], body["state"], body["attemptNo"]) == ("completed", "COMPLETED", 2)
    assert body["payload"]["metrics"] == {"n_points3d": 7}
    assert body["payload"]["artifacts"] == artifacts


def test_a_typed_failure_is_reported_with_its_diagnostic() -> None:
    diag = {"code": "NO_FEATURES", "message": "m", "advice": "a"}
    result = {"ok": False, "attemptNo": 1, "diagnostic": diag, "summary": {"timings": {"total": 3}}}
    out, reported, _heavy = asyncio.run(run_workflow(WORKFLOW_CONTRACT, result))
    assert out == {"status": "failed"}
    (body,) = reported
    assert (body["type"], body["state"]) == ("failed", "FAILED")
    assert body["payload"]["diagnostic"] == diag and body["payload"]["timings"] == {"total": 3}
