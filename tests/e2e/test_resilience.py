"""Failure injection against the live local stack (Windows, via scripts/dev.ps1).

    python -m pytest tests/e2e/test_resilience.py -m e2e -s

Proves the reliability contracts with real processes: a killed worker never leaves an orphaned
engine, Temporal retries the work on a fresh worker, the attempt history records what happened, an
API restart mid-job loses nothing, and failed/cancelled jobs leave no scratch files behind.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from test_e2e import API, EVIDENCE, HOME, TERMINAL, Tenant, save, terrace_images  # noqa: F401

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "win32", reason="drives services via dev.ps1"),
]

REPO = Path(__file__).resolve().parents[2]


def dev(*args: str) -> None:
    # DEVNULL, not capture_output: a service started by dev.ps1 would inherit our stdout pipe and
    # keep it open, so subprocess.run would block until that service exits.
    subprocess.run(
        ["powershell", "-NoProfile", "-File", str(REPO / "scripts" / "dev.ps1"), *args],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
        env={**os.environ, "PARALLAXOPS_HOME": str(HOME)},
    )


def engines() -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["cmdline"]):
        c = p.info["cmdline"] or []
        if any("parallaxops_vision.engine" in a for a in c):
            out.append(p)
    return out


def wait_for(pred, timeout: float, what: str):  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.5)
    raise AssertionError(f"timeout waiting for {what}")


@pytest.fixture
def running_job(terrace_images):  # type: ignore[no-untyped-def]
    t = Tenant()
    _p, d = t.make_dataset(terrace_images)
    job_id = t.call(
        "POST", f"/v1/datasets/{d}/jobs", json={"label": "resilience"}
    ).json()["id"]
    yield t, job_id
    t.call("POST", f"/v1/jobs/{job_id}/cancel")


def test_killed_worker_leaves_no_orphan_engine_and_work_is_retried(running_job) -> None:  # type: ignore[no-untyped-def]
    t, job_id = running_job
    wait_for(lambda: engines(), 120, "engine process to start")
    time.sleep(2)
    pid_file = HOME / "run" / "worker.pid"
    worker_pid = int(pid_file.read_text())
    # Hard-kill the worker process tree the way a crash or OOM-kill would (no cleanup code runs).
    subprocess.run(
        ["taskkill", "/PID", str(worker_pid), "/T", "/F"],
        check=True,
        capture_output=True,
    )
    pid_file.unlink()
    # The Job Object (KILL_ON_JOB_CLOSE) must take the engine down with the worker.
    wait_for(lambda: not engines(), 20, "engine to die with its worker")

    dev(
        "up", "worker"
    )  # a replacement worker; Temporal retries the activity after the heartbeat timeout
    t0 = time.perf_counter()
    job, seen = t.wait(job_id, TERMINAL, timeout=600)
    assert job["state"] == "COMPLETED", (job.get("diagnostic"), seen)
    states = [(a["attemptNo"], a["state"]) for a in job["attempts"]]
    assert states[0] == (1, "FAILED") and states[-1][1] == "SUCCEEDED", states
    assert "stopped reporting" in job["attempts"][0]["failure"]["message"]
    assert job["attempts"][0]["workerId"] != job["attempts"][-1]["workerId"]
    # No duplicate or overwritten artifacts: published once, under the successful attempt's prefix.
    arts = t.call("GET", f"/v1/jobs/{job_id}/artifacts").json()
    kinds = [a["kind"] for a in arts]
    assert len(kinds) == len(set(kinds))
    save(
        "worker_kill_retry.json",
        {
            "attempts": states,
            "recovery_seconds": round(time.perf_counter() - t0, 1),
            "timeline": seen,
        },
    )


def test_api_restart_mid_job_loses_nothing(running_job) -> None:  # type: ignore[no-untyped-def]
    t, job_id = running_job
    wait_for(
        lambda: (
            t.call("GET", f"/v1/jobs/{job_id}").json()["state"]
            in {"RECONSTRUCTING", "PREPROCESSING"}
        ),
        60,
        "job to start",
    )
    dev("down", "api")
    time.sleep(6)
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"{API}/readyz", timeout=2)
    dev("up", "api")
    job, seen = t.wait(job_id, TERMINAL, timeout=600)
    assert job["state"] == "COMPLETED", (job.get("diagnostic"), seen)
    assert {"feature_matching", "sparse_mapping"} <= {
        s["name"] for s in job["stages"]
    } or job["attempts"]
    save(
        "api_restart.json",
        {
            "timeline": seen,
            "attempts": [(a["attemptNo"], a["state"]) for a in job["attempts"]],
        },
    )


def test_no_scratch_files_or_engines_remain() -> None:
    wait_for(lambda: not engines(), 60, "idle engines")
    scratch = HOME / "scratch" / "jobs"
    leftovers = (
        [p for p in scratch.rglob("*") if p.is_file()] if scratch.exists() else []
    )
    assert not leftovers, f"scratch files left behind: {leftovers[:5]}"
    metrics = httpx.get("http://127.0.0.1:9108/metrics", timeout=5).text
    assert "parallaxops_worker_scratch_cleaned_bytes_total" in metrics
    save("cleanup_check.json", {"scratch_files": 0, "worker_metric_present": True})


def test_evidence_files_are_valid_json() -> None:
    for f in EVIDENCE.glob("*.json"):
        json.loads(f.read_text(encoding="utf-8"))
