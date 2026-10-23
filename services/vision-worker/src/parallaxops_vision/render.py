"""Static renders of a real reconstruction (matplotlib; works headless on every OS).

Open3D offscreen rendering is unreliable on Windows, so these are projections of the actual PLY
and camera poses, not a separate visualization pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import open3d as o3d


def _frustum(center: np.ndarray, rot_cw: np.ndarray, size: float) -> np.ndarray:
    """Camera frustum corner points in world coordinates (camera looks along +z)."""
    corners = (
        np.array([[-1, -0.66, 1.4], [1, -0.66, 1.4], [1, 0.66, 1.4], [-1, 0.66, 1.4]], dtype=float)
        * size
    )
    return (rot_cw.T @ corners.T).T + center


def render_all(ply: Path, cameras_json: Path, out_dir: Path, title: str = "") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pcd = o3d.io.read_point_cloud(ply)
    xyz = np.asarray(pcd.points)
    rgb = np.asarray(pcd.colors)
    cams = json.loads(cameras_json.read_text(encoding="utf-8"))["images"]
    centers = np.array([c["center"] for c in cams])
    rots = [np.array(c["cam_from_world"]["rotation_matrix"]) for c in cams]

    # Frame on the camera rig plus the near-field scene. Far background points (sky, horizon)
    # are cropped from the *picture only*; the PLY itself is never filtered here.
    mid = centers.mean(axis=0)
    cam_spread = float(np.linalg.norm(centers.max(0) - centers.min(0))) or 1.0
    dist = np.linalg.norm(xyz - mid, axis=1)
    half = max(float(np.percentile(dist, 75)), 1.5 * cam_spread)
    keep = dist <= half * 1.2
    size = half * 0.07

    views = {"oblique": (22, -60), "top": (88, -90), "front": (4, -90)}
    written: list[Path] = []
    for name, (elev, azim) in views.items():
        fig = plt.figure(figsize=(8, 6.4), dpi=150)
        ax = fig.add_subplot(111, projection="3d")
        ax.scatter(
            xyz[keep, 0],
            xyz[keep, 1],
            xyz[keep, 2],
            c=rgb[keep],
            s=1.2,
            depthshade=False,
            linewidths=0,
        )
        for c, r in zip(centers, rots, strict=True):
            f = _frustum(c, r, size)
            for a, b in zip(f, np.roll(f, -1, axis=0), strict=True):
                ax.plot(*zip(a, b, strict=True), color="#d62728", lw=0.7)
            for p in f:
                ax.plot(*zip(c, p, strict=True), color="#d62728", lw=0.5)
        ax.set_xlim(mid[0] - half, mid[0] + half)
        ax.set_ylim(mid[1] - half, mid[1] + half)
        ax.set_zlim(mid[2] - half, mid[2] + half)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_xlabel("x (model units)")
        ax.set_ylabel("y")
        ax.set_zlabel("z")
        ax.set_title(
            f"{title} {name}: {int(keep.sum())} of {len(xyz)} sparse points shown "
            f"(near-field crop), {len(cams)} cameras".strip(),
            fontsize=9,
        )
        fig.tight_layout()
        p = out_dir / f"render_{name}.png"
        fig.savefig(p)
        plt.close(fig)
        written.append(p)
    return written


def render_metrics_figure(report: dict[str, Any], out: Path) -> Path:
    m = report["metrics"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4), dpi=150)
    h = m["track_length_hist"]
    axes[0].bar(list(h.keys()), list(h.values()), color="#3b6fb6")
    axes[0].set_title("Track length (images per 3D point)")
    axes[0].set_xlabel("observations per point")
    axes[0].set_ylabel("3D points")
    labels = ["mean", "median", "p90", "max"]
    vals = [m[f"reproj_error_px_{k}"] for k in labels]
    axes[1].bar(labels, vals, color="#c2703d")
    axes[1].set_title("Reprojection error (px, working res.)")
    st = report["timings_seconds"]
    names = [k for k in st if k != "total"]
    axes[2].barh(names, [st[k] for k in names], color="#5a8f5a")
    axes[2].set_title(f"Stage time (s) — total {st['total']:.1f}")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out
