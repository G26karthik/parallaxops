"""Run the pre-registered baseline-vs-controller experiment (docs/EXPERIMENT_PLAN.md).

    python benchmarks/run_experiment.py --scene terrace --reps 3 --budget 100
    python benchmarks/run_experiment.py --scene relief --reps 1 --budget 120 --max-image-size 1200

Resumable: finished (scene, variant, arm, rep) combinations in runs.jsonl are skipped. Arms are
interleaved per (variant, rep) so slow drift (thermals, background load) hits both equally.
Results: benchmarks/results/gate4/runs.jsonl and meta.json. Nothing here edits or filters results.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

sys.path.insert(0, str(Path(__file__).parent))
from variants import home, make  # noqa: E402

from parallaxops_vision.config import ReconstructionConfig  # noqa: E402
from parallaxops_vision.controller import run_controlled  # noqa: E402
from parallaxops_vision.envinfo import host_info, tool_versions  # noqa: E402
from parallaxops_vision.pipeline import reconstruct  # noqa: E402

OUT = Path(__file__).parent / "results" / "gate4"
VARIANTS = ["clean", "blur", "overlap", "repeats", "light", "moving"]


def row_from_report(base: dict[str, Any], rep: dict[str, Any]) -> dict[str, Any]:
    out = {**base, "status": rep["status"]}
    if rep["status"] != "completed":
        out["diagnostic"] = rep["diagnostic"]["code"]
        return out
    m, g = rep["metrics"], rep.get("ground_truth_eval") or {}
    out.update(
        n_registered=m["n_registered"],
        n_input_images=m["n_input_images"],
        registered_fraction=m["registered_fraction"],
        n_points3d=m["n_points3d"],
        reproj_mean_px=m["reproj_error_px_mean"],
        reproj_median_px=m["reproj_error_px_median"],
        track_mean=m["track_length_mean"],
        tri_angle_median_deg=m["triangulation_angle_deg_median"],
        quality=m["quality"]["level"],
        gt_matched=g.get("n_matched_to_gt"),
        gt_inliers=g.get("n_inliers"),
        gt_ate_rmse_all=g.get("ate_rmse_all"),
        gt_ate_over_spread=g.get("ate_rmse_all_over_spread"),
        gt_rot_median_deg=g.get("rotation_error_deg_median"),
        gt_spread=g.get("gt_camera_spread"),
        gt_per_image_pos_err={
            k: v["position_error"] for k, v in (g.get("per_image") or {}).items()
        },
    )
    return out


def run_one(
    scene: str,
    variant: str,
    arm: str,
    rep: int,
    a: argparse.Namespace,
    vdir: Path,
    gt: Path,
) -> dict[str, Any]:
    cfg = ReconstructionConfig(seed=rep, max_image_size=a.max_image_size)
    out = Path(os.environ.get("TEMP", ".")) / f"pxo_exp_{scene}_{variant}_{arm}_{rep}"
    shutil.rmtree(out, ignore_errors=True)
    base = {
        "scene": scene,
        "variant": variant,
        "arm": arm,
        "rep": rep,
        "seed": rep,
        "max_image_size": a.max_image_size,
        "at": datetime.now(UTC).isoformat(),
    }
    t0 = time.perf_counter()
    try:
        if arm == "baseline":
            rep_ = reconstruct(
                vdir / variant, out, cfg, gt_images_txt=gt, force=True, render=False
            )
            wall = time.perf_counter() - t0
            res = rep_.get("resources") or {}
            row = row_from_report(base, rep_)
            row.update(
                wall_s=round(wall, 2),
                cpu_s=res.get("engine_cpu_seconds"),
                peak_rss_bytes=res.get("peak_rss_bytes_process_tree"),
                attempts=1,
                strategies=[],
            )
        else:
            final = run_controlled(
                vdir / variant,
                out,
                cfg,
                gt_images_txt=gt,
                baseline_budget_s=a.budget,
                force=True,
            )
            wall = time.perf_counter() - t0
            res = [e.get("resources") or {} for e in final["log"] if "attempt" in e]
            row = row_from_report(base, final["report"])
            row.update(
                wall_s=round(wall, 2),
                cpu_s=round(sum(r.get("engine_cpu_seconds") or 0 for r in res), 1),
                peak_rss_bytes=max(
                    (r.get("peak_rss_bytes_process_tree") or 0 for r in res),
                    default=None,
                ),
                attempts=final["attempts"],
                best_attempt=final["best_attempt"],
                strategies=[
                    e["decision"].split(" ")[1]
                    for e in final["log"]
                    if e.get("decision", "").startswith("next:")
                ],
                pre_run=[e["action"] for e in final["log"] if "action" in e],
                advice=final["advice"],
            )
    except Exception as exc:  # noqa: BLE001 - an unexpected crash is itself a result and is recorded
        row = {
            **base,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "wall_s": round(time.perf_counter() - t0, 2),
        }
    finally:
        shutil.rmtree(out, ignore_errors=True)
    return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scene", default="terrace")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument(
        "--budget",
        type=float,
        default=100.0,
        help="baseline wall-clock budget in seconds (controller may use 3x)",
    )
    p.add_argument("--max-image-size", type=int, default=1600)
    p.add_argument("--variants", nargs="*", default=VARIANTS)
    p.add_argument(
        "--out-file",
        default="runs.jsonl",
        help="runs_posthoc.jsonl for non-pre-registered re-runs",
    )
    a = p.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    runs = OUT / a.out_file
    done = (
        {
            (r["scene"], r["variant"], r["arm"], r["rep"])
            for r in map(json.loads, runs.read_text().splitlines())
        }
        if runs.exists()
        else set()
    )
    scene_root = home() / "data" / "eth3d" / a.scene / a.scene
    vdir = home() / "data" / "variants" / a.scene
    if not (vdir / "variants.json").exists():
        make(scene_root / "images" / "dslr_images", vdir)
    shutil.copyfile(vdir / "variants.json", OUT / f"variants_{a.scene}.json")
    gt = scene_root / "dslr_calibration_jpg" / "images.txt"

    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"runs": []}
    meta.update(host=host_info(), tools=tool_versions(), plan="docs/EXPERIMENT_PLAN.md")
    meta["runs"].append(
        {
            "scene": a.scene,
            "reps": a.reps,
            "budget_s": a.budget,
            "max_image_size": a.max_image_size,
            "started": datetime.now(UTC).isoformat(),
            "free_ram_gib_at_start": round(
                psutil.virtual_memory().available / 1024**3, 1
            ),
        }
    )
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    for variant in a.variants:
        for rep in range(a.reps):
            for arm in ("baseline", "controller"):
                if (a.scene, variant, arm, rep) in done:
                    continue
                row = run_one(a.scene, variant, arm, rep, a, vdir, gt)
                with runs.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                print(
                    f"{variant:8s} {arm:10s} rep{rep}: {row['status']:9s} wall={row.get('wall_s')} reg={row.get('registered_fraction')} ate/spread={row.get('gt_ate_over_spread')}",
                    flush=True,
                )


if __name__ == "__main__":
    main()
