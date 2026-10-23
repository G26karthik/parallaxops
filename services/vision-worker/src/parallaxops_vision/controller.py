"""Quality-aware reconstruction controller.

A small, transparent policy around the same engine the baseline uses. It never sees ground truth.
It runs the baseline configuration first and only spends more compute when the measured result is
weak, choosing from a short fixed menu of recovery strategies and recording why. See
docs/EXPERIMENT_PLAN.md for the pre-registered evaluation.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import ReconstructionConfig
from .export import publish_atomic, write_json
from .pipeline import reconstruct
from .qc import run_qc

ACCEPT_REGISTERED = 0.90
ACCEPT_REPROJ_PX = 1.5
WEAK_TRI_ANGLE_DEG = 3.0
MAX_BLUR_EXCLUDE_FRACTION = 0.30
MIN_IMAGES_AFTER_EXCLUSION = 8
MAX_ATTEMPTS = 3
BUDGET_MULTIPLE = 3.0

# Rough relative cost of a strategy vs the previous attempt (feature extraction and matching
# dominate and are repeated; no database reuse yet). Used only to skip strategies that cannot fit.
COST_FACTOR = {
    "more_features_and_scale": 2.2,
    "global_mapper": 1.0,
    "opencv_camera": 1.0,
    "exclude_high_residual": 0.9,
}
FAMILY_MENU = {
    "registration": ["more_features_and_scale", "global_mapper"],
    "reprojection": ["opencv_camera", "exclude_high_residual"],
}


@dataclass
class Strategy:
    name: str
    reason: str
    config: dict[str, Any]
    exclude: list[str]


def diagnose(report: dict[str, Any]) -> tuple[str, str]:
    """Return (family, explanation) from a pipeline report. Uses measured metrics only."""
    if report["status"] != "completed":
        code = report["diagnostic"]["code"]
        if code in {
            "NO_FEATURES",
            "INSUFFICIENT_MATCHES",
            "NO_INITIAL_PAIR",
            "SFM_NO_MODEL",
            "LOW_REGISTRATION",
            "FRAGMENTED_MODEL",
        }:
            return "registration", f"attempt failed with {code}"
        return "stop", f"attempt failed with non-recoverable {code}"
    m = report["metrics"]
    if m["registered_fraction"] < ACCEPT_REGISTERED:
        return (
            "registration",
            f"only {m['registered_fraction']:.0%} of images registered (< {ACCEPT_REGISTERED:.0%})",
        )
    if m["reproj_error_px_mean"] > ACCEPT_REPROJ_PX:
        return (
            "reprojection",
            f"mean reprojection {m['reproj_error_px_mean']:.2f} px > {ACCEPT_REPROJ_PX} px",
        )
    return "accept", "registered fraction and reprojection error are within thresholds"


def next_strategy(
    family: str,
    report: dict[str, Any],
    cfg: ReconstructionConfig,
    used: set[str],
    excluded: set[str],
) -> Strategy | None:
    """Pick the first unused strategy of the diagnosed family. Pure function: easy to test."""
    for name in FAMILY_MENU.get(family, []):
        if name in used:
            continue
        if name == "more_features_and_scale":
            return Strategy(
                name,
                "more features and +25% working size to match harder image pairs",
                {
                    "max_num_features": min(cfg.max_num_features * 2, 16384),
                    "max_image_size": min(round(cfg.max_image_size * 1.25), 3200),
                },
                [],
            )
        if name == "global_mapper":
            return Strategy(
                name,
                "global mapper (GLOMAP) can register views incremental SfM drops",
                {"mapper": "global"},
                [],
            )
        if name == "opencv_camera":
            return Strategy(
                name,
                "model radial and tangential distortion (OPENCV) to reduce residuals",
                {"camera_model": "OPENCV"},
                [],
            )
        if name == "exclude_high_residual" and report["status"] == "completed":
            rows = [
                r for r in report["metrics"]["per_image_error"] if r["mean_error_px"] is not None
            ]
            if len(rows) < 8:
                continue
            med = float(np.median([r["mean_error_px"] for r in rows]))
            bad = sorted(
                (r for r in rows if r["mean_error_px"] > 2 * med and r["name"] not in excluded),
                key=lambda r: -r["mean_error_px"],
            )[: max(1, int(0.2 * len(rows)))]
            if bad:
                return Strategy(
                    name,
                    f"exclude {len(bad)} images with residual > 2x median ({med:.2f} px)",
                    {},
                    [b["name"] for b in bad],
                )
    return None


def score(report: dict[str, Any]) -> tuple[float, float]:
    """Higher is better: registered fraction first, then lower reprojection error."""
    if report["status"] != "completed":
        return (-1.0, 0.0)
    m = report["metrics"]
    return (m["registered_fraction"], -m["reproj_error_px_mean"])


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    if report["status"] != "completed":
        return {"status": "failed", "diagnostic": report["diagnostic"]["code"]}
    m = report["metrics"]
    return {
        "status": "completed",
        "registered_fraction": m["registered_fraction"],
        "n_points3d": m["n_points3d"],
        "reproj_error_px_mean": m["reproj_error_px_mean"],
        "triangulation_angle_deg_median": m["triangulation_angle_deg_median"],
    }


def run_controlled(
    input_dir: Path,
    output_dir: Path,
    base: ReconstructionConfig | None = None,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    baseline_budget_s: float = 600.0,
    gt_images_txt: Path | None = None,
    force: bool = False,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the controller. `gt_images_txt` is passed through for *evaluation only*."""
    base = base or ReconstructionConfig()
    t_start = time.perf_counter()
    budget = baseline_budget_s * BUDGET_MULTIPLE
    work = output_dir.parent / f".{output_dir.name}.attempts-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    log: list[dict[str, Any]] = []
    cfg = base.model_copy(deep=True)
    excluded: set[str] = set(cfg.exclude_images)

    # Pre-run policy: drop blurry images when that is affordable (input QC heuristic).
    qc = run_qc(input_dir, cfg)
    blurry = [i.name for i in qc.images if "possibly_blurry" in i.flags]
    usable = len([i for i in qc.images if "exact_duplicate" not in i.flags])
    if (
        blurry
        and len(blurry) <= MAX_BLUR_EXCLUDE_FRACTION * usable
        and usable - len(blurry) >= MIN_IMAGES_AFTER_EXCLUSION
    ):
        excluded |= set(blurry)
        cfg = cfg.model_copy(update={"exclude_images": sorted(excluded)})
        log.append(
            {
                "step": "pre_run",
                "action": "exclude_blurry",
                "reason": f"{len(blurry)}/{usable} images flagged blurry (heuristic)",
                "excluded": blurry,
            }
        )

    attempts: list[tuple[dict[str, Any], Path, ReconstructionConfig]] = []
    used: set[str] = set()
    last_wall = 0.0
    for n in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        out = work / f"attempt-{n}"
        report = reconstruct(
            input_dir,
            out,
            cfg,
            gt_images_txt=gt_images_txt,
            force=True,
            render=False,
            on_event=on_event,
        )
        last_wall = time.perf_counter() - t0
        attempts.append((report, out, cfg))
        family, why = diagnose(report)
        entry: dict[str, Any] = {
            "attempt": n,
            "config": cfg.model_dump(mode="json", exclude={"budgets", "qc"}),
            "wall_seconds": round(last_wall, 1),
            "resources": report.get("resources"),
            "outcome": _summary(report),
            "diagnosis": {"family": family, "why": why},
        }
        log.append(entry)
        if family in {"accept", "stop"}:
            entry["decision"] = "stop: accepted" if family == "accept" else f"stop: {why}"
            break
        strat = next_strategy(family, report, cfg, used, excluded)
        if strat is None:
            entry["decision"] = "stop: no strategy left for this diagnosis"
            break
        remaining = budget - (time.perf_counter() - t_start)
        projected = last_wall * COST_FACTOR[strat.name]
        if projected > remaining:
            entry["decision"] = (
                f"stop: {strat.name} projected {projected:.0f}s > remaining budget {remaining:.0f}s"
            )
            break
        if n == max_attempts:
            entry["decision"] = "stop: attempt limit reached"
            break
        used.add(strat.name)
        excluded |= set(strat.exclude)
        cfg = cfg.model_copy(update={**strat.config, "exclude_images": sorted(excluded)})
        entry["decision"] = f"next: {strat.name} ({strat.reason})"

    best_i = max(range(len(attempts)), key=lambda i: score(attempts[i][0]))
    best_report, best_dir, _ = attempts[best_i]
    final = {
        "arm": "controller",
        "attempts": len(attempts),
        "best_attempt": best_i + 1,
        "total_wall_seconds": round(time.perf_counter() - t_start, 2),
        "budget_seconds": budget,
        "log": log,
        "advice": None,
    }
    if best_report["status"] == "completed":
        m = best_report["metrics"]
        if (
            m["registered_fraction"] < ACCEPT_REGISTERED
            or m["triangulation_angle_deg_median"] < WEAK_TRI_ANGLE_DEG
        ):
            final["advice"] = (
                "The controller could not fully recover this capture. Take more photographs with "
                "sideways movement between views and consistent lighting."
            )
    staging = work / "final"
    shutil.copytree(best_dir, staging)
    write_json(staging / "controller_log.json", final)
    publish_atomic(staging, output_dir, overwrite=force)
    shutil.rmtree(work, ignore_errors=True)
    final["report"] = best_report
    return final


def load_log(output_dir: Path) -> dict[str, Any]:
    return json.loads((output_dir / "controller_log.json").read_text(encoding="utf-8"))
