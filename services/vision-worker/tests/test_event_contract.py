"""The worker's event stream, pinned to a golden file that the API replays.

`apps/api/test/worker_events.test.ts` feeds the same file to the real API. Each side had its own
hand-written idea of what the other sends, which is how phase changes once broke without a red test
(commit bd011f9). Now both read one file. Regenerate with UPDATE_GOLDEN=1, then run both suites.

The lifecycle lines before and after the pipeline events mirror `run_reconstruction`; the pipeline
events have the shape `pipeline.reconstruct` emits through `stage()` and `sub()`. Terminal events
(`completed`, `failed`) are built by the workflow and are not part of this file.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

from parallaxops_vision.worker import activities
from parallaxops_vision.worker.api_client import ApiClient, EventSender
from parallaxops_vision.worker.settings import Settings

GOLDEN = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "worker_events.json"
)
JOB = "00000000-0000-4000-8000-000000000001"

PIPELINE_EVENTS: list[dict[str, Any]] = [
    {"stage": "PREPROCESSING", "state": "start", "elapsed": 0.0},
    {"stage": "SUBSTAGE", "state": "start", "substage": "qc", "seconds": None},
    {"stage": "SUBSTAGE", "state": "end", "substage": "qc", "seconds": 0.5},
    {"stage": "PREPROCESSING", "state": "end", "elapsed": 1.0},
    {"stage": "RECONSTRUCTING", "state": "start", "elapsed": 1.0},
    {"stage": "SUBSTAGE", "state": "start", "substage": "feature_extraction", "seconds": None},
    {
        "stage": "RECONSTRUCTING",
        "state": "heartbeat",
        "rss_bytes": 1000,
        "disk_bytes": 2000,
        "elapsed": 7.0,
    },
    {"stage": "SUBSTAGE", "state": "end", "substage": "feature_extraction", "seconds": 6.5},
    {"stage": "EVALUATING", "state": "start", "elapsed": 50.0},
    {"stage": "SUBSTAGE", "state": "start", "substage": "metrics", "seconds": None},
    {"stage": "SUBSTAGE", "state": "end", "substage": "metrics", "seconds": 0.7},
]


def worker_stream() -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"applied": True})

    api = ApiClient(Settings(api_url="http://api.test"), transport=httpx.MockTransport(handler))
    sender = EventSender(api, JOB, 1, "w-golden")

    async def emit(type_: str, **fields: Any) -> None:
        sender.send(type_, **fields)

    async def run() -> None:
        await emit("attempt_started", state="PREPROCESSING")
        await emit("stage_started", stage="download_inputs")
        await emit("stage_finished", stage="download_inputs", payload={"seconds": 0.2})
        for ev in PIPELINE_EVENTS:
            await activities._forward(emit, ev, -100.0)  # -100: the 5 s heartbeat throttle is open
        await emit("stage_started", stage="publish_artifacts")
        await emit("stage_finished", stage="publish_artifacts", payload={"seconds": 0.1})

    asyncio.run(run())
    return sent


def test_worker_stream_matches_the_golden_file_the_api_replays() -> None:
    stream = worker_stream()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        # bytes, not write_text: Windows would write CRLF. Run `pnpm exec biome check --write` after.
        GOLDEN.write_bytes((json.dumps(stream, indent=2) + "\n").encode("utf-8"))
    assert stream == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_stream_announces_every_phase_the_api_needs() -> None:
    # Without a state-carrying event per phase the API refuses `completed` (QUEUED/PREPROCESSING).
    states = [e["state"] for e in worker_stream() if e.get("state")]
    assert states == ["PREPROCESSING", "PREPROCESSING", "RECONSTRUCTING", "EVALUATING"]
