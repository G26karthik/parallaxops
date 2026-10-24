"""Masked vs unmasked SfM on a real scene with moving people (plan: docs/EXPERIMENT_MASKING.md).

    python benchmarks/masking/run_masking.py prepare   # frames, ground truth, person masks
    python benchmarks/masking/run_masking.py run --reps 3
    python benchmarks/masking/run_masking.py summarize

Data: TUM RGB-D freiburg3_walking_xyz (fetched by hand into $PARALLAXOPS_HOME/data/tum, never
committed). Arms are interleaved per repetition so slow drift hits both equally. Results go to
benchmarks/results/masking/runs.jsonl; nothing here edits or filters them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_experiment import row_from_report  # noqa: E402
from variants import home  # noqa: E402

from parallaxops_vision.config import Budgets, ReconstructionConfig  # noqa: E402
from parallaxops_vision.envinfo import host_info, tool_versions  # noqa: E402
from parallaxops_vision.groundtruth import load_colmap_text_images, quat_to_rot  # noqa: E402
from parallaxops_vision.pipeline import reconstruct  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "results" / "masking"
ROOT = home() / "data" / "tum" / "walking_xyz"
SUBSET = home() / "data" / "tum" / "walking_xyz_subset"
SEGMENTER = Path(__file__).with_name("segment_masks.py")
MAX_GT_GAP_S = 0.02


def read_table(path: Path) -> list[list[str]]:
    return [
        ln.split()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def prepare(step: int, segment: bool = True) -> None:
    frames = read_table(ROOT / "rgb.txt")[::step]
    gt = np.array([[float(v) for v in r] for r in read_table(ROOT / "groundtruth.txt")])
    images = SUBSET / "images"
    # Without segmentation only the images and the ground truth are rebuilt; masks are kept.
    shutil.rmtree(SUBSET if segment else images, ignore_errors=True)
    images.mkdir(parents=True)
    # COLMAP images.txt: comment lines, then two lines per image (pose, then its 2D points).
    lines = ["# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME (world -> camera)"]
    kept = 0
    for idx, (ts, rel) in enumerate(frames, start=1):
        name = f"f{idx:04d}.png"
        shutil.copyfile(ROOT / rel, images / name)
        k = int(np.abs(gt[:, 0] - float(ts)).argmin())
        if abs(gt[k, 0] - float(ts)) > MAX_GT_GAP_S:
            continue
        t_wc, (qx, qy, qz, qw) = gt[k, 1:4], gt[k, 4:8]
        # TUM stores the camera pose in the world frame; COLMAP wants world -> camera.
        r_cw = quat_to_rot(qw, -qx, -qy, -qz)
        t_cw = -r_cw @ t_wc
        lines += [
            f"{idx} {qw} {-qx} {-qy} {-qz} {t_cw[0]} {t_cw[1]} {t_cw[2]} 1 {name}",
            "",
        ]
        kept += 1
    (SUBSET / "images.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    parsed = load_colmap_text_images(SUBSET / "images.txt")
    assert len(parsed) == kept, f"ground truth file reads back {len(parsed)} poses, wrote {kept}"
    print(
        f"{len(frames)} frames (every {step}th), {kept} with ground truth within {MAX_GT_GAP_S} s"
    )
    if not segment:
        return

    venv = home() / "venv-mask" / "Scripts" / "python.exe"
    t0 = time.perf_counter()
    subprocess.run(
        [
            str(venv),
            "-I",
            str(SEGMENTER),
            "--images",
            str(images),
            "--out",
            str(SUBSET / "masks"),
        ],
        check=True,
    )
    print(f"segmentation wall {time.perf_counter() - t0:.1f} s")


def run(reps: int, max_image_size: int, arms: tuple[str, ...]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    runs = OUT / "runs.jsonl"
    done = (
        {(r["arm"], r["rep"]) for r in map(json.loads, runs.read_text().splitlines())}
        if runs.exists()
        else set()
    )
    log = json.loads((SUBSET / "masks" / "masks_log.json").read_text(encoding="utf-8"))
    shutil.copyfile(SUBSET / "masks" / "masks_log.json", OUT / "masks_log.json")
    (OUT / "meta.json").write_text(
        json.dumps(
            {
                "host": host_info(),
                "tools": tool_versions(),
                "plan": "docs/EXPERIMENT_MASKING.md",
                "started": datetime.now(UTC).isoformat(),
                "segmentation_model": log["model"],
                "segmentation_seconds": log["total_seconds"],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    for rep in range(reps):
        for arm in arms:
            if (arm, rep) in done:
                continue
            # The default cap of 80 images rejects the 86 frames outright (TOO_MANY_IMAGES); the
            # cap is a safety limit, not a reconstruction setting, so it is the only deviation.
            cfg = ReconstructionConfig(
                seed=rep, max_image_size=max_image_size, budgets=Budgets(max_images=100)
            )
            out = Path(tempfile.gettempdir()) / f"pxo_mask_{arm}_{rep}"
            shutil.rmtree(out, ignore_errors=True)
            base: dict[str, Any] = {
                "arm": arm,
                "rep": rep,
                "seed": rep,
                "at": datetime.now(UTC).isoformat(),
            }
            t0 = time.perf_counter()
            try:
                rep_ = reconstruct(
                    SUBSET / "images",
                    out,
                    cfg,
                    gt_images_txt=SUBSET / "images.txt",
                    masks_dir=SUBSET / "masks" if arm == "masked" else None,
                    force=True,
                    render=False,
                )
                row = row_from_report(base, rep_)
                row["wall_s"] = round(time.perf_counter() - t0, 2)
                row["stages_s"] = rep_.get("timings")
                row["masking"] = rep_.get("masking")
                row["cpu_s"] = (rep_.get("resources") or {}).get("engine_cpu_seconds")
            except Exception as exc:  # noqa: BLE001 - a crash is a result and is recorded
                row = {
                    **base,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                shutil.rmtree(out, ignore_errors=True)
            with runs.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(
                f"{arm:9s} rep{rep}: {row['status']:9s} wall={row.get('wall_s')} "
                f"reg={row.get('registered_fraction')} pts={row.get('n_points3d')} "
                f"reproj={row.get('reproj_mean_px')} ate/spread={row.get('gt_ate_over_spread')}",
                flush=True,
            )


def summarize() -> None:
    rows = [json.loads(ln) for ln in (OUT / "runs.jsonl").read_text().splitlines()]
    log = json.loads((OUT / "masks_log.json").read_text(encoding="utf-8"))
    fields = (
        "registered_fraction",
        "n_points3d",
        "reproj_mean_px",
        "gt_ate_rmse_all",
        "gt_ate_over_spread",
        "wall_s",
    )
    summary: dict[str, Any] = {"n_rows": len(rows), "arms": {}}
    for arm in ("unmasked", "masked"):
        ok = [r for r in rows if r["arm"] == arm and r["status"] == "completed"]
        summary["arms"][arm] = {
            "runs": sum(1 for r in rows if r["arm"] == arm),
            "completed": len(ok),
            **{
                f: {
                    "mean": round(mean(r[f] for r in ok if r.get(f) is not None), 5),
                    "min": min(r[f] for r in ok if r.get(f) is not None),
                    "max": max(r[f] for r in ok if r.get(f) is not None),
                }
                for f in fields
                if any(r.get(f) is not None for r in ok)
            },
        }
    fr = [v["ignored_fraction"] for v in log["per_image"].values()]
    summary["masking"] = {
        "segmentation_seconds": log["total_seconds"],
        "images": log["n_images"],
        "ignored_fraction_mean": round(float(np.mean(fr)), 4),
        "ignored_fraction_max": round(float(np.max(fr)), 4),
        "images_with_any_mask": sum(1 for v in fr if v > 0),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("--step", type=int, default=10, help="keep every Nth frame (30 Hz source)")
    pp.add_argument(
        "--skip-masks", action="store_true", help="rebuild images and ground truth, keep masks"
    )
    pr = sub.add_parser("run")
    pr.add_argument("--reps", type=int, default=3)
    pr.add_argument("--max-image-size", type=int, default=1600)
    pr.add_argument(
        "--arms",
        nargs="+",
        choices=("unmasked", "masked"),
        default=["unmasked", "masked"],
        help="run only some arms (the plan's feasibility run uses: --reps 1 --arms unmasked)",
    )
    sub.add_parser("summarize")
    a = p.parse_args()
    if a.cmd == "prepare":
        prepare(a.step, segment=not a.skip_masks)
    elif a.cmd == "run":
        run(a.reps, a.max_image_size, tuple(a.arms))
    else:
        summarize()


if __name__ == "__main__":
    main()
