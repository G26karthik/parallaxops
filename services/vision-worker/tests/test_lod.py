"""PXLD multi-resolution point-cloud files (worker/lod.py)."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np
import open3d as o3d
import pytest

from parallaxops_vision.worker.lod import GRIDS, LOD_MAGIC, build_lod, lod_order

GOLDEN = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures" / "tiny.pxld"
RECORD = np.dtype([("p", "<f4", 3), ("c", "u1", 3), ("pad", "u1")])


def write_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray | None) -> Path:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz)
    if rgb is not None:
        pcd.colors = o3d.utility.Vector3dVector(rgb)
    o3d.io.write_point_cloud(str(path), pcd)
    return path


def read_lod(path: Path) -> tuple[np.ndarray, list[int], np.ndarray]:
    """Reference decoder: the format description in lod.py, spelled out once more."""
    raw = path.read_bytes()
    magic, version, total, levels = struct.unpack_from("<4sIII", raw, 0)
    assert (magic, version) == (LOD_MAGIC, 1)
    ends = list(struct.unpack_from(f"<{levels}I", raw, 40))
    head = -(-(40 + 4 * levels) // 16) * 16
    rec = np.frombuffer(raw, dtype=RECORD, offset=head)
    assert len(rec) == total == ends[-1]
    assert raw[40 + 4 * levels : head] == bytes(head - 40 - 4 * levels)
    return rec["p"], ends, rec["c"]


def scene(n: int = 30_000) -> np.ndarray:
    """A noisy plane, a dense blob and a few far outliers: uneven density on purpose."""
    rng = np.random.default_rng(7)
    plane = np.c_[rng.uniform(0, 10, (n // 2, 2)), rng.normal(0, 0.01, n // 2)]
    blob = rng.normal([5, 5, 3], 0.2, (n // 2 - 5, 3))
    far = rng.uniform(40, 50, (5, 3))
    return np.vstack([plane, blob, far])


def test_levels_are_disjoint_and_complete(tmp_path: Path) -> None:
    xyz = scene()
    rgb = np.random.default_rng(1).uniform(size=xyz.shape)
    ply = write_ply(tmp_path / "a.ply", xyz, rgb)
    out = tmp_path / "a.pxld"
    assert build_lod(ply, out) == len(xyz)
    p, ends, c = read_lod(out)
    assert ends == sorted(set(ends)) and len(ends) > 3
    # Same points, and each colour still sits on its own point: compare in a canonical order.
    src = o3d.io.read_point_cloud(str(ply))
    want_p = np.asarray(src.points).astype("f4")
    want_c = (np.clip(np.asarray(src.colors), 0, 1) * 255).round().astype("u1")
    a, b = np.lexsort(want_p.T), np.lexsort(p.T)
    assert np.array_equal(p[b], want_p[a])
    assert np.array_equal(c[b], want_c[a])


def test_every_level_boundary_covers_every_occupied_cell() -> None:
    """The defining property: a coarse prefix misses no region the full cloud occupies."""
    xyz = scene()
    order, ends = lod_order(xyz)
    lo = xyz.min(axis=0)
    extent = (xyz.max(axis=0) - lo).max()

    def cells(points: np.ndarray, g: int) -> set[tuple[int, int, int]]:
        ijk = np.minimum(((points - lo) / (extent / g)).astype(np.int64), g - 1)
        return {tuple(v) for v in ijk.tolist()}

    for g, end in zip(GRIDS, ends, strict=False):
        assert cells(xyz[order[:end]], g) == cells(xyz, g), f"grid {g}"
    assert sorted(order.tolist()) == list(range(len(xyz)))  # a permutation: nothing lost, no copy


def test_output_is_deterministic(tmp_path: Path) -> None:
    ply = write_ply(tmp_path / "d.ply", scene(2000), None)
    build_lod(ply, tmp_path / "1.pxld")
    build_lod(ply, tmp_path / "2.pxld")
    assert (tmp_path / "1.pxld").read_bytes() == (tmp_path / "2.pxld").read_bytes()


def test_point_cap_cuts_from_the_fine_end(tmp_path: Path) -> None:
    ply = write_ply(tmp_path / "c.ply", scene(5000), None)
    full = tmp_path / "full.pxld"
    capped = tmp_path / "capped.pxld"
    build_lod(ply, full)
    assert build_lod(ply, capped, max_points=1500) == 1500
    p_full, ends_full, _ = read_lod(full)
    p_cap, ends_cap, _ = read_lod(capped)
    assert ends_cap[-1] == 1500 and all(e < 1500 for e in ends_cap[:-1])
    assert ends_cap[:-1] == [e for e in ends_full if e < 1500][: len(ends_cap) - 1]
    assert np.array_equal(p_cap, p_full[:1500])


def test_uncoloured_and_degenerate_clouds(tmp_path: Path) -> None:
    plain = tmp_path / "plain.pxld"
    build_lod(write_ply(tmp_path / "plain.ply", scene(500), None), plain)
    assert (read_lod(plain)[2] == 200).all()  # neutral grey, not a crash
    one = tmp_path / "one.pxld"
    assert build_lod(write_ply(tmp_path / "one.ply", np.zeros((1, 3)), None), one) == 1
    assert read_lod(one)[1] == [1]
    empty = write_ply(tmp_path / "e.ply", np.zeros((0, 3)), None)
    with pytest.raises(ValueError, match="no points"):
        build_lod(empty, tmp_path / "e.pxld")


def golden_cloud() -> tuple[np.ndarray, np.ndarray]:
    """A 6x6 lattice plus one far point: the big bounding box makes the lattice span levels."""
    ij = np.array([(i, j) for i in range(6) for j in range(6)], dtype=np.float64)
    xyz = np.vstack([np.c_[ij * 0.25, (ij[:, 0] * 0.5) % 1.0], [[10.0, 10.0, 10.0]]])
    rgb = np.vstack([np.c_[ij[:, 0] / 5, ij[:, 1] / 5, np.full(len(ij), 0.5)], [[1.0, 1.0, 1.0]]])
    return xyz, rgb


def test_golden_file_pins_the_format_for_the_web_decoder(tmp_path: Path) -> None:
    """apps/web decodes the same bytes (test/lod.test.ts). Regenerate with UPDATE_GOLDEN=1."""
    xyz, rgb = golden_cloud()
    out = tmp_path / "tiny.pxld"
    build_lod(write_ply(tmp_path / "tiny.ply", xyz, rgb), out)
    assert len(read_lod(out)[1]) >= 3  # the fixture must exercise more than one level
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(out.read_bytes())
    assert out.read_bytes() == GOLDEN.read_bytes()
