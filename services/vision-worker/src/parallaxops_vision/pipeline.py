"""End-to-end reconstruction: QC -> prepare -> supervised SfM engine -> metrics -> export.

One call produces either a published artifact directory with status "completed", or a published
directory with status "failed" carrying a typed diagnostic. Nothing is ever half-published.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pycolmap

from . import CONTRACT_VERSION, __version__
from .config import ReconstructionConfig
from .diagnostics import Diagnostic, DiagnosticCode, ReconstructionError
from .envinfo import host_info, tool_versions
from .export import (
    build_manifest,
    camera_poses,
    publish_atomic,
    write_json,
    write_ply,
)
from .groundtruth import evaluate_poses, load_colmap_text_images
from .metrics import SfMMetrics, compute_metrics
from .prep import PreparedImage, prepare_images
from .qc import QCReport, run_qc
from .supervisor import SupervisedResult, run_supervised

EventCb = Callable[[dict[str, Any]], None]

LIMITATIONS = [
    "Sparse reconstruction only: points are triangulated SIFT features, not a dense surface.",
    "Scale is arbitrary (monocular input). No metric distances are claimed.",
    "Image QC flags are heuristics and do not prove overlap or reconstructability.",
    "Reprojection error is a self-consistency proxy, not ground-truth accuracy.",
]


def _classify_kill(res: SupervisedResult) -> Diagnostic | None:
    mapping = {
        "timeout": (DiagnosticCode.TIMEOUT, "Reconstruction exceeded the wall-clock budget."),
        "cancelled": (DiagnosticCode.CANCELLED, "Reconstruction was cancelled."),
        "memory": (
            DiagnosticCode.MEMORY_BUDGET_EXCEEDED,
            "Reconstruction exceeded the RAM budget.",
        ),
        "disk": (DiagnosticCode.DISK_BUDGET_EXCEEDED, "Workspace exceeded the disk budget."),
    }
    if res.killed_reason:
        code, msg = mapping[res.killed_reason]
        return Diagnostic.of(
            code, msg, wall_seconds=res.wall_seconds, peak_rss_bytes=res.peak_rss_bytes
        )
    return None


def _eval_gt(
    rec: pycolmap.Reconstruction, prepared: list[PreparedImage], images_txt: Path
) -> dict[str, Any]:
    gt = load_colmap_text_images(images_txt)
    by_safe = {p.safe_name: Path(p.original_name).name for p in prepared}
    est = {}
    for im in rec.images.values():
        if im.has_pose and im.name in by_safe:
            pose = im.cam_from_world()
            est[by_safe[im.name]] = (pose.rotation.matrix(), pose.translation)
    return evaluate_poses(est, gt).model_dump()


def reconstruct(
    input_dir: Path,
    output_dir: Path,
    cfg: ReconstructionConfig | None = None,
    *,
    cancel: threading.Event | None = None,
    on_event: EventCb | None = None,
    gt_images_txt: Path | None = None,
    force: bool = False,
    keep_database: bool = False,
    render: bool = True,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Run the full pipeline. Returns the report dict (also written as report.json)."""
    cfg = cfg or ReconstructionConfig()
    emit = on_event or (lambda _e: None)
    output_dir = output_dir.resolve()
    attempt = uuid.uuid4().hex[:12]
    staging = (workspace_root or output_dir.parent) / f".{output_dir.name}.staging-{attempt}"
    work = staging / "_work"
    work.mkdir(parents=True)
    t_start = time.perf_counter()
    timings: dict[str, float] = {}
    report: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "worker_version": __version__,
        "attempt_id": attempt,
        "status": "failed",
        "config": cfg.model_dump(mode="json"),
        "limitations": LIMITATIONS,
        "warnings": [],
    }
    qc: QCReport | None = None
    prepared: list[PreparedImage] = []
    invocation: dict[str, Any] = {}

    def stage(name: str, state: str, **extra: Any) -> None:
        emit(
            {
                "stage": name,
                "state": state,
                "elapsed": round(time.perf_counter() - t_start, 2),
                **extra,
            }
        )

    try:
        stage("PREPROCESSING", "start")
        t = time.perf_counter()
        qc = run_qc(input_dir, cfg)
        timings["qc"] = round(time.perf_counter() - t, 3)
        write_json(staging / "qc_report.json", qc.model_dump(mode="json"))
        t = time.perf_counter()
        prepared = prepare_images(input_dir, qc, cfg, work / "images")
        timings["prepare_images"] = round(time.perf_counter() - t, 3)
        report["qc_summary"] = {
            "n_images": len(qc.images),
            "n_used": len(prepared),
            "warnings": qc.warnings,
            "flag_counts": {
                f: sum(1 for i in qc.images if f in i.flags)
                for f in (
                    "possibly_blurry",
                    "dark",
                    "overexposed",
                    "near_duplicate",
                    "exact_duplicate",
                )
            },
        }
        stage("PREPROCESSING", "end")

        stage("RECONSTRUCTING", "start")
        request = {
            "workspace": str(work),
            "images": str(work / "images"),
            "config": cfg.model_dump(mode="json"),
            "remaining_seconds": max(
                60, int(cfg.budgets.wall_clock_seconds - (time.perf_counter() - t_start))
            ),
        }
        (work / "request.json").write_text(json.dumps(request), encoding="utf-8")

        def tick(info: dict[str, Any]) -> None:
            stage("RECONSTRUCTING", "heartbeat", **info)

        res = run_supervised(
            [sys.executable, "-I", "-m", "parallaxops_vision.engine", str(work / "request.json")],
            cwd=work,
            log_path=work / "engine.log",
            timeout_s=max(30, cfg.budgets.wall_clock_seconds - (time.perf_counter() - t_start)),
            max_rss_bytes=cfg.budgets.max_rss_bytes,
            workspace=work,
            max_workspace_bytes=cfg.budgets.max_workspace_bytes,
            cancel=cancel,
            on_tick=tick,
        )
        report["resources"] = {
            "engine_wall_seconds": res.wall_seconds,
            "engine_cpu_seconds": res.cpu_seconds,
            "peak_rss_bytes_process_tree": res.peak_rss_bytes,
            "note": "RSS is sampled every 0.5 s; peak may be slightly under-reported. No GPU used.",
        }
        killed = _classify_kill(res)
        if killed:
            raise ReconstructionError(killed)
        result_path = work / "engine_result.json"
        if res.returncode != 0 or not result_path.exists():
            tb = work / "engine_traceback.txt"
            raise ReconstructionError(
                Diagnostic.of(
                    DiagnosticCode.ENGINE_CRASH,
                    f"Engine exited with code {res.returncode}.",
                    stage="engine",
                    traceback_tail=tb.read_text(encoding="utf-8")[-1500:]
                    if tb.exists()
                    else res.log_tail[-1500:],
                )
            )
        eng = json.loads(result_path.read_text(encoding="utf-8"))
        if not eng.get("ok"):
            raise ReconstructionError(Diagnostic.model_validate(eng["diagnostic"]))
        invocation = eng["invocation"]
        timings.update({k: v for k, v in eng["stages_seconds"].items()})
        report["matching_graph"] = eng["matching_graph"]
        report["models_found"] = eng["models"]
        stage("RECONSTRUCTING", "end")

        stage("EVALUATING", "start")
        t = time.perf_counter()
        rec = pycolmap.Reconstruction(work / eng["best_model_dir"])
        metrics: SfMMetrics = compute_metrics(rec, prepared)
        timings["metrics"] = round(time.perf_counter() - t, 3)
        report["metrics"] = metrics.model_dump(mode="json")
        if metrics.n_registered < 3 or metrics.registered_fraction < 0.3:
            raise ReconstructionError(
                Diagnostic.of(
                    DiagnosticCode.LOW_REGISTRATION,
                    f"Only {metrics.n_registered}/{metrics.n_input_images} images registered.",
                    stage="sparse_mapping",
                    unregistered=metrics.unregistered[:30],
                )
            )
        if len(eng["models"]) > 1 and metrics.registered_fraction < 0.6:
            report["warnings"].append(
                Diagnostic.of(
                    DiagnosticCode.FRAGMENTED_MODEL,
                    f"{len(eng['models'])} disconnected models; best covers "
                    f"{metrics.registered_fraction:.0%} of images.",
                ).model_dump(mode="json")
            )
        if cfg.mapper == "incremental" and metrics.registered_fraction < 0.9:
            report["warnings"].append(
                Diagnostic.of(
                    DiagnosticCode.LOW_REGISTRATION,
                    f"{len(metrics.unregistered)} images did not register.",
                    unregistered=metrics.unregistered[:30],
                ).model_dump(mode="json")
            )

        # --- export into the staging dir ---
        t = time.perf_counter()
        n_pts = write_ply(rec, staging / "points_sparse.ply")
        write_json(staging / "cameras.json", camera_poses(rec, prepared))
        sparse_out = staging / "colmap" / "sparse" / "0"
        sparse_out.mkdir(parents=True)
        rec.write_binary(str(sparse_out))
        timings["export"] = round(time.perf_counter() - t, 3)
        if gt_images_txt is not None:
            report["ground_truth_eval"] = _eval_gt(rec, prepared, gt_images_txt)
        report["points_exported"] = n_pts
        report["status"] = "completed"
        stage("EVALUATING", "end")
    except ReconstructionError as exc:
        report["diagnostic"] = exc.diagnostic.model_dump(mode="json")
        stage("FAILED", "end", code=str(exc.diagnostic.code))
    except Exception as exc:
        report["diagnostic"] = Diagnostic.of(
            DiagnosticCode.INTERNAL, f"{type(exc).__name__}: {exc}"
        ).model_dump(mode="json")
        stage("FAILED", "end", code="INTERNAL")

    timings["total"] = round(time.perf_counter() - t_start, 3)
    report["timings_seconds"] = timings

    # Logs and engine trace are kept for BOTH outcomes (reproducibility, debugging).
    logs = staging / "logs"
    logs.mkdir(exist_ok=True)
    for name in ("engine.log", "progress.jsonl", "engine_result.json", "engine_traceback.txt"):
        src = work / name
        if src.exists():
            shutil.copyfile(src, logs / name)
    if keep_database and (work / "database.db").exists():
        shutil.copyfile(work / "database.db", staging / "colmap" / "database.db")

    if report["status"] == "completed" and render:
        from .render import render_all, render_metrics_figure

        try:
            render_all(staging / "points_sparse.ply", staging / "cameras.json", staging / "renders")
            render_metrics_figure(report, staging / "renders" / "metrics.png")
        except Exception as exc:
            report["warnings"].append(
                Diagnostic.of(DiagnosticCode.INTERNAL, f"render failed: {exc}").model_dump(
                    mode="json"
                )
            )

    write_json(staging / "report.json", report)
    shutil.rmtree(work, ignore_errors=True)
    manifest = build_manifest(
        artifacts_dir=staging,
        config=cfg.model_dump(mode="json"),
        tools=tool_versions(),
        host=host_info(),
        prepared=prepared,
        invocation=invocation,
        status=report["status"],
    )
    write_json(staging / "manifest.json", manifest)
    publish_atomic(staging, output_dir, overwrite=force)
    return report
