"""Turn a finished pipeline output directory into published, checksummed object-store artifacts."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any

from .lod import build_lod
from .storage import Store

# kind -> (relative path inside the pipeline output dir, content type)
FILES: dict[str, tuple[str, str]] = {
    "points_ply": ("points_sparse.ply", "application/octet-stream"),
    "points_dense_ply": ("points_dense.ply", "application/octet-stream"),
    "cameras": ("cameras.json", "application/json"),
    "report": ("report.json", "application/json"),
    "qc_report": ("qc_report.json", "application/json"),
    "render_oblique": ("renders/render_oblique.png", "image/png"),
    "render_top": ("renders/render_top.png", "image/png"),
    "render_front": ("renders/render_front.png", "image/png"),
    "render_metrics": ("renders/metrics.png", "image/png"),
    "engine_log": ("logs/engine.log", "text/plain"),
}


def zip_dir(src: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())


def publish(store: Store, out_dir: Path, prefix: str, scratch: Path) -> list[dict[str, Any]]:
    """Upload artifacts under an attempt-specific prefix. The manifest goes last so a reader that
    sees it can rely on every other object already being present."""
    staged: dict[str, tuple[Path, str]] = {}
    for kind, (rel, ctype) in FILES.items():
        p = out_dir / rel
        if p.exists():
            staged[kind] = (p, ctype)
    # Multi-resolution streams for the browser (see lod.py): coarse first, nothing is thrown away.
    for kind, ply, name in (
        ("points_lod", "points_sparse.ply", "points_lod.bin"),
        ("points_dense_lod", "points_dense.ply", "points_dense_lod.bin"),
    ):
        if (out_dir / ply).exists():
            build_lod(out_dir / ply, scratch / name)
            staged[kind] = (scratch / name, "application/octet-stream")
    sparse = out_dir / "colmap" / "sparse" / "0"
    if sparse.exists():
        archive = scratch / "colmap_sparse.zip"
        zip_dir(sparse, archive)
        staged["colmap_sparse"] = (archive, "application/zip")
    staged["manifest"] = (out_dir / "manifest.json", "application/json")

    published: list[dict[str, Any]] = []
    for kind, (path, ctype) in staged.items():  # dict order: manifest is last
        key = f"{prefix}{path.name}"
        size, sha = store.upload(path, key, ctype)
        published.append(
            {"kind": kind, "key": key, "bytes": size, "sha256": sha, "contentType": ctype}
        )
    return published


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def remove_tree(path: Path) -> int:
    """Delete a scratch directory and return the bytes freed (reported to metrics)."""
    freed = dir_bytes(path) if path.exists() else 0
    shutil.rmtree(path, ignore_errors=True)
    return freed
