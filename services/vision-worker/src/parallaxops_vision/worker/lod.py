"""Level-of-detail point cloud for the browser (format PXLD, version 1).

The file is one stream, ordered coarse to fine. Every prefix that ends on a level boundary is a
spatially even subsample of the whole cloud, so a client can draw level 0 after a few kilobytes,
keep refining while bytes arrive, draw a coarse prefix while the camera moves, and stop reading
when its point budget is reached. Levels are disjoint: together they are the complete input.

Layout, little endian:

    "PXLD" | u32 version | u32 total points | u32 level count
    f32 bbox_min[3] | f32 bbox_max[3]
    u32 level_end[level count]        cumulative point count at the end of each level
    zero padding to a multiple of 16 bytes
    total * 16 bytes: f32 x, f32 y, f32 z, u8 r, u8 g, u8 b, u8 0

Level k keeps, among the points not used by earlier levels, the one nearest to the centre of each
occupied cell of a GRIDS[k]^3 grid over the bounding cube. Whatever is left after the last grid
forms the final level. The web viewer decodes this layout in apps/web/src/lib/lod.ts; the golden
file in packages/contracts/fixtures keeps the two implementations in step.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import open3d as o3d

LOD_MAGIC = b"PXLD"
LOD_VERSION = 1
LOD_MAX_POINTS = 8_000_000
GRIDS = (16, 32, 64, 128, 256, 512, 1024)


def lod_order(xyz: np.ndarray, grids: tuple[int, ...] = GRIDS) -> tuple[np.ndarray, list[int]]:
    """Point order (coarse to fine) and the cumulative point count at the end of each level."""
    n = len(xyz)
    pts = np.asarray(xyz, dtype=np.float64)
    lo = pts.min(axis=0)
    extent = max(float((pts.max(axis=0) - lo).max()), 1e-12)
    remaining = np.arange(n)
    parts: list[np.ndarray] = []
    ends: list[int] = []
    taken = 0
    for g in grids:
        if remaining.size == 0:
            break
        p = pts[remaining]
        cell = extent / g
        ijk = np.minimum(((p - lo) / cell).astype(np.int64), g - 1)
        key = (ijk[:, 0] * g + ijk[:, 1]) * g + ijk[:, 2]
        dist = (((lo + (ijk + 0.5) * cell) - p) ** 2).sum(axis=1)
        by_cell = np.lexsort((dist, key))  # cell first, then nearest to the cell centre; stable
        first = np.ones(by_cell.size, dtype=bool)
        first[1:] = key[by_cell][1:] != key[by_cell][:-1]
        chosen = by_cell[first]
        parts.append(remaining[chosen])
        taken += chosen.size
        ends.append(taken)
        keep = np.ones(remaining.size, dtype=bool)
        keep[chosen] = False
        remaining = remaining[keep]
    if remaining.size:
        parts.append(remaining)
        ends.append(n)
    return np.concatenate(parts), ends


def build_lod(ply: Path, out: Path, max_points: int = LOD_MAX_POINTS) -> int:
    """Write the PXLD file for a PLY point cloud and return the number of points it holds.

    More than max_points are cut from the fine end, which keeps the coverage even.
    """
    pcd = o3d.io.read_point_cloud(ply)
    xyz = np.asarray(pcd.points, dtype=np.float64)
    if len(xyz) == 0:
        raise ValueError(f"{ply.name} holds no points")
    order, ends = lod_order(xyz)
    if len(order) > max_points:
        order = order[:max_points]
        ends = [e for e in ends if e < max_points] + [max_points]
    if pcd.has_colors():
        rgb = (np.clip(np.asarray(pcd.colors), 0, 1) * 255).round().astype("u1")
    else:
        rgb = np.full((len(xyz), 3), 200, dtype="u1")

    rec = np.zeros(
        len(order),
        dtype=[("p", "<f4", 3), ("c", "u1", 3), ("pad", "u1")],
    )
    rec["p"] = xyz[order]
    rec["c"] = rgb[order]
    head = struct.pack(
        f"<4sIII6f{len(ends)}I",
        LOD_MAGIC,
        LOD_VERSION,
        len(order),
        len(ends),
        *rec["p"].min(axis=0),
        *rec["p"].max(axis=0),
        *ends,
    )
    with out.open("wb") as f:
        f.write(head + bytes(-len(head) % 16))
        f.write(rec.tobytes())
    return len(order)
