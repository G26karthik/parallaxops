"""Temporal activities. The heavy activity runs the whole attempt locally (download, reconstruct,
publish) so scratch never needs to be shared between hosts; the workflow reports terminal state."""

from __future__ import annotations

import asyncio
import contextlib
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..config import ReconstructionConfig
from ..diagnostics import Diagnostic, DiagnosticCode
from ..pipeline import reconstruct
from ..qc import run_qc
from . import WORKFLOW_CONTRACT, metrics
from .api_client import ApiClient, EventSender
from .artifacts import publish, remove_tree
from .scratch import claim
from .settings import Settings
from .storage import InputManifest, Store

RETRYABLE = {DiagnosticCode.ENGINE_CRASH.value, DiagnosticCode.INTERNAL.value}
_CAMEL = {
    "maxImageSize": "max_image_size",
    "maxNumFeatures": "max_num_features",
    "mapper": "mapper",
    "matching": "matching",
    "cameraModel": "camera_model",
    "seed": "seed",
}


class Runtime:
    """Process-wide dependencies, created once in main()."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api = ApiClient(settings)
        self.store = Store(settings)


_rt: Runtime | None = None


def set_runtime(rt: Runtime) -> None:
    global _rt
    _rt = rt


def rt() -> Runtime:
    if _rt is None:
        raise RuntimeError("worker runtime not initialised")
    return _rt


def to_config(api_config: dict[str, Any]) -> ReconstructionConfig:
    return ReconstructionConfig(**{_CAMEL[k]: v for k, v in api_config.items() if k in _CAMEL})


def safe_name(index: int, filename: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:100] or "image"
    return f"{index:04d}_{base}"


def check_contract(inp: dict[str, Any]) -> None:
    if inp.get("contract") != WORKFLOW_CONTRACT:
        raise ApplicationError(
            f"unsupported workflow contract {inp.get('contract')!r}; worker speaks {WORKFLOW_CONTRACT}",
            type="ContractError",
            non_retryable=True,
        )


def load_manifest(store: Store, inp: dict[str, Any]) -> InputManifest:
    """Fetch the input manifest and refuse anything outside the job's own tenant/dataset prefix."""
    m = InputManifest.model_validate(store.get_json(inp["inputManifestKey"]))
    if (
        m.contract != WORKFLOW_CONTRACT
        or m.tenantId != inp["tenantId"]
        or m.datasetId != inp["datasetId"]
    ):
        raise ApplicationError(
            "manifest does not match workflow input", type="ContractError", non_retryable=True
        )
    root = f"tenants/{m.tenantId}/datasets/{m.datasetId}/images/"
    for img in m.images:
        if not img.key.startswith(root) or ".." in img.key:
            raise ApplicationError(
                f"image key outside tenant prefix: {img.key!r}",
                type="ContractError",
                non_retryable=True,
            )
    return m


def download_inputs(store: Store, manifest: InputManifest, dest: Path) -> list[str]:
    """Download images in manifest order (capture order) and verify each against its SHA-256."""
    dest.mkdir(parents=True, exist_ok=True)
    bad: list[str] = []
    for i, img in enumerate(manifest.images, start=1):
        sha = store.download(img.key, dest / safe_name(i, img.filename))
        if sha != img.sha256:
            bad.append(img.filename)
    return bad


async def _beat(detail: str = "") -> None:
    activity.heartbeat(detail)


@activity.defn(name="run_reconstruction")
async def run_reconstruction(inp: dict[str, Any]) -> dict[str, Any]:
    check_contract(inp)
    r = rt()
    info = activity.info()
    attempt = info.attempt
    job_id = inp["jobId"]
    max_attempts = int(inp.get("maxAttempts", 1))
    sender = EventSender(r.api, job_id, attempt, r.settings.worker_id)
    attempt_dir = r.settings.scratch_dir / job_id / f"attempt-{attempt}"
    in_dir, out_dir = attempt_dir / "in", attempt_dir / "out"
    claim(attempt_dir)  # records this process as owner so a sweeper can reap it if we are killed
    cancel = threading.Event()
    outcome = "failed"

    async def emit(type_: str, **fields: Any) -> dict[str, Any] | None:
        """Progress is best-effort: a flaky API must never fail a reconstruction."""
        try:
            return await asyncio.to_thread(lambda: sender.send(type_, **fields))
        except Exception as exc:
            activity.logger.warning("event %s not delivered: %s", type_, exc)
            return None

    metrics.ACTIVE_HEAVY.inc()
    try:
        await emit("attempt_started", state="PREPROCESSING")
        manifest = await asyncio.to_thread(load_manifest, r.store, inp)
        await _beat("downloading inputs")
        await emit("stage_started", stage="download_inputs")
        t0 = time.perf_counter()
        bad = await asyncio.to_thread(download_inputs, r.store, manifest, in_dir)
        await emit(
            "stage_finished",
            stage="download_inputs",
            payload={"seconds": round(time.perf_counter() - t0, 3)},
        )
        if bad:
            diag = Diagnostic.of(
                DiagnosticCode.UNREADABLE_IMAGES,
                f"{len(bad)} stored image(s) failed checksum verification after download.",
                files=bad[:10],
            ).model_dump(mode="json")
            return {"ok": False, "attemptNo": attempt, "diagnostic": diag, "summary": {}}

        events: deque[dict[str, Any]] = deque()
        cfg = to_config(inp["config"])
        task = asyncio.create_task(
            asyncio.to_thread(
                reconstruct,
                in_dir,
                out_dir,
                cfg,
                cancel=cancel,
                on_event=events.append,
                render=True,
                workspace_root=attempt_dir,
            )
        )
        last_beat = 0.0
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=1.0)
                while events:
                    ev = events.popleft()
                    last_beat = await _forward(emit, ev, last_beat)
                await _beat("reconstructing")
            while events:
                await _forward(emit, events.popleft(), last_beat)
        except asyncio.CancelledError:
            outcome = "cancelled"
            cancel.set()  # supervisor kills the engine process tree
            await asyncio.wait({task}, timeout=30)
            raise
        report: dict[str, Any] = task.result()

        res = report.get("resources") or {}
        if "peak_rss_bytes_process_tree" in res:
            metrics.PEAK_RSS.set(res["peak_rss_bytes_process_tree"])
        summary = {
            k: report.get(v)
            for k, v in {
                "metrics": "metrics",
                "quality": None,
                "groundTruth": "ground_truth_eval",
                "timings": "timings_seconds",
                "resources": "resources",
                "warnings": "warnings",
            }.items()
            if v
        }
        summary["quality"] = (report.get("metrics") or {}).get("quality")
        for stage_name, secs in (report.get("timings_seconds") or {}).items():
            if stage_name != "total":
                metrics.STAGE_SECONDS.labels(stage=stage_name).observe(secs)

        if report["status"] != "completed":
            diag = report["diagnostic"]
            if diag["code"] in RETRYABLE:
                will_retry = attempt < max_attempts
                await emit("attempt_failed", payload={"diagnostic": diag, "willRetry": will_retry})
                outcome = "retry" if will_retry else "failed"
                if will_retry:
                    raise ApplicationError(diag["message"], type=diag["code"])
            return {"ok": False, "attemptNo": attempt, "diagnostic": diag, "summary": summary}

        await _beat("publishing artifacts")
        await emit("stage_started", stage="publish_artifacts")
        t0 = time.perf_counter()
        prefix = f"tenants/{inp['tenantId']}/jobs/{job_id}/attempt-{attempt}/"
        artifacts = await asyncio.to_thread(publish, r.store, out_dir, prefix, attempt_dir)
        metrics.ARTIFACT_BYTES.inc(sum(a["bytes"] for a in artifacts))
        await emit(
            "stage_finished",
            stage="publish_artifacts",
            payload={"seconds": round(time.perf_counter() - t0, 3)},
        )
        outcome = "ok"
        return {"ok": True, "attemptNo": attempt, "summary": summary, "artifacts": artifacts}
    finally:
        metrics.ACTIVE_HEAVY.dec()
        freed = remove_tree(attempt_dir)
        with contextlib.suppress(OSError):
            attempt_dir.parent.rmdir()  # drop the now-empty job directory (no-op if another attempt exists)
        metrics.CLEANUP_BYTES.labels(outcome=outcome).inc(freed)
        metrics.ACTIVITIES.labels(activity="run_reconstruction", outcome=outcome).inc()


async def _forward(emit: Any, ev: dict[str, Any], last_beat: float) -> float:
    """Map pipeline events onto API events. Top-level phases move the job state; sub-stages feed
    the timeline; resource heartbeats are throttled to one per 5 s."""
    stage, state = ev["stage"], ev["state"]
    if stage == "SUBSTAGE":
        if state == "start":
            await emit("stage_started", stage=ev["substage"])
        else:
            await emit(
                "stage_finished", stage=ev["substage"], payload={"seconds": ev.get("seconds")}
            )
    elif state == "start" and stage in {"PREPROCESSING", "RECONSTRUCTING", "EVALUATING"}:
        await emit("heartbeat", state=stage)
    elif state == "heartbeat" and time.monotonic() - last_beat >= 5:
        await emit(
            "heartbeat",
            payload={
                "rssBytes": ev.get("rss_bytes"),
                "diskBytes": ev.get("disk_bytes"),
                "elapsed": ev.get("elapsed"),
            },
        )
        return time.monotonic()
    return last_beat


@activity.defn(name="report_event")
async def report_event(body: dict[str, Any]) -> dict[str, Any]:
    """Durable terminal/lifecycle reporting. Temporal retries until the API acknowledges it."""
    result = await asyncio.to_thread(rt().api.event, body)
    metrics.ACTIVITIES.labels(activity="report_event", outcome="ok").inc()
    return result


@activity.defn(name="run_preflight")
async def run_preflight(inp: dict[str, Any]) -> dict[str, Any]:
    check_contract(inp)
    r = rt()
    scratch = r.settings.scratch_dir / "preflight" / f"{inp['datasetId']}-{int(time.time())}"
    outcome = "failed"
    claim(scratch)
    try:
        manifest = await asyncio.to_thread(load_manifest, r.store, inp)
        await _beat("downloading")
        bad = await asyncio.to_thread(download_inputs, r.store, manifest, scratch / "in")
        if bad:
            diag = Diagnostic.of(
                DiagnosticCode.UNREADABLE_IMAGES,
                f"{len(bad)} stored image(s) failed checksum verification.",
            )
            return {"ok": False, "error": diag.model_dump(mode="json")}
        from ..diagnostics import ReconstructionError

        try:
            qc = await asyncio.to_thread(run_qc, scratch / "in", ReconstructionConfig())
        except ReconstructionError as exc:
            outcome = "ok"
            return {"ok": False, "error": exc.diagnostic.model_dump(mode="json")}
        outcome = "ok"
        report = qc.model_dump(mode="json")
        # Display names: strip the capture-order prefix added for sorting.
        for im in report["images"]:
            im["name"] = re.sub(r"^\d{4}_", "", im["name"])
        return {"ok": True, "report": report}
    finally:
        metrics.CLEANUP_BYTES.labels(outcome=outcome).inc(remove_tree(scratch))
        metrics.ACTIVITIES.labels(activity="run_preflight", outcome=outcome).inc()


@activity.defn(name="report_preflight")
async def report_preflight(body: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(rt().api.preflight_result, body)
