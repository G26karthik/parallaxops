"""Worker entrypoint: `python -m parallaxops_vision.worker.main`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from . import TASK_QUEUES, metrics
from .activities import (
    Runtime,
    report_event,
    report_preflight,
    run_preflight,
    run_reconstruction,
    set_runtime,
)
from .settings import Settings
from .workflows import PreflightWorkflow, ReconstructionWorkflow


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = Settings()
    set_runtime(Runtime(s))
    metrics.serve(s.metrics_port)
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)

    workflows_worker = Worker(
        client,
        task_queue=TASK_QUEUES["workflows"],
        workflows=[ReconstructionWorkflow, PreflightWorkflow],
    )
    light_worker = Worker(
        client,
        task_queue=TASK_QUEUES["light"],
        activities=[report_event, run_preflight, report_preflight],
        max_concurrent_activities=4,
    )
    # The capacity gate: this worker runs at most `heavy_slots` reconstructions at once. Further
    # jobs wait in Temporal's task queue (observable as queue wait), not in worker memory.
    heavy_worker = Worker(
        client,
        task_queue=TASK_QUEUES["heavy"],
        activities=[run_reconstruction],
        max_concurrent_activities=s.heavy_slots,
        graceful_shutdown_timeout=timedelta(seconds=60),
    )
    logging.getLogger(__name__).info(
        "worker %s up: heavy_slots=%d metrics=:%d temporal=%s",
        s.worker_id,
        s.heavy_slots,
        s.metrics_port,
        s.temporal_address,
    )
    await asyncio.gather(workflows_worker.run(), light_worker.run(), heavy_worker.run())


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
