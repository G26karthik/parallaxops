"""Artifact export: PLY (Open3D), camera poses JSON, manifest with checksums, atomic publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import open3d as o3d
import pycolmap

from . import CONTRACT_VERSION
from .prep import PreparedImage


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_ply(rec: pycolmap.Reconstruction, path: Path) -> int:
    pts = list(rec.points3D.values())
    if not pts:
        raise ValueError("model has no 3D points")
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.array([p.xyz for p in pts], dtype=np.float64))
    pcd.colors = o3d.utility.Vector3dVector(
        np.array([p.color for p in pts], dtype=np.float64) / 255.0
    )
    if not o3d.io.write_point_cloud(path, pcd, write_ascii=False):
        raise OSError("Open3D failed to write PLY")
    return len(pts)


def camera_poses(rec: pycolmap.Reconstruction, prepared: list[PreparedImage]) -> dict[str, Any]:
    by_safe = {p.safe_name: p for p in prepared}
    images = []
    for im in sorted(rec.images.values(), key=lambda x: x.name):
        if not im.has_pose:
            continue
        pose = im.cam_from_world()
        rot = pose.rotation.matrix()
        quat = pose.rotation.quat  # (x, y, z, w) in COLMAP/Eigen storage order
        prep = by_safe.get(im.name)
        images.append(
            {
                "image": prep.original_name if prep else im.name,
                "safe_name": im.name,
                "camera_id": int(im.camera_id),
                "cam_from_world": {
                    "rotation_matrix": [[float(x) for x in row] for row in rot],
                    "quaternion_xyzw": [float(x) for x in quat],
                    "translation": [float(x) for x in pose.translation],
                },
                "center": [float(x) for x in im.projection_center()],
                "viewing_direction": [float(x) for x in im.viewing_direction()],
                "num_points3d": int(im.num_points3D),
            }
        )
    cams = []
    for cid, c in sorted(rec.cameras.items()):
        cams.append(
            {
                "camera_id": int(cid),
                "model": c.model_name,
                "width": int(c.width),
                "height": int(c.height),
                "params": [float(x) for x in c.params],
            }
        )
    registered = {i["safe_name"] for i in images}
    return {
        "contract_version": CONTRACT_VERSION,
        "coordinate_frame": (
            "COLMAP world frame; camera looks along +z, x right, y down. "
            "Units are arbitrary (monocular SfM, no metric scale)."
        ),
        "cameras": cams,
        "images": images,
        "unregistered": [p.original_name for p in prepared if p.safe_name not in registered],
    }


def build_manifest(
    *,
    artifacts_dir: Path,
    config: dict[str, Any],
    tools: dict[str, str],
    host: dict[str, Any],
    prepared: list[PreparedImage],
    invocation: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    files = []
    for p in sorted(artifacts_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            files.append(
                {
                    "path": p.relative_to(artifacts_dir).as_posix(),
                    "bytes": p.stat().st_size,
                    "sha256": sha256_file(p),
                }
            )
    return {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "config": config,
        "tools": tools,
        "host": host,
        "inputs": [
            {
                "image": p.original_name,
                "safe_name": p.safe_name,
                "sha256": p.sha256,
                "original_size": list(p.original_size),
                "prepared_size": list(p.prepared_size),
                "scale": p.scale,
            }
            for p in prepared
        ],
        "invocation": invocation,
        "artifacts": files,
    }


def publish_atomic(staging: Path, final: Path, *, overwrite: bool) -> None:
    """Make staging visible as final in a single rename. Never exposes a half-written directory."""
    if final.exists():
        if not overwrite and any(final.iterdir()):
            raise FileExistsError(f"{final.name} exists; pass --force to replace it")
        trash = final.with_name(f"{final.name}.old-{uuid.uuid4().hex[:8]}")
        os.replace(final, trash)
        os.replace(staging, final)
        shutil.rmtree(trash, ignore_errors=True)
    else:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")
