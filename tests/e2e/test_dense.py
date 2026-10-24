"""Real GPU dense reconstruction through the whole platform (needs the CUDA COLMAP build + NVIDIA GPU).

    python -m pytest tests/e2e/test_dense.py -m "e2e and gpu" -s

About 13 minutes on an RTX 4060 for 23 images at 1000 px. Evidence: benchmarks/results/gate5/.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import httpx
import pytest
from test_e2e import HOME, TERMINAL, Tenant

pytestmark = [pytest.mark.e2e, pytest.mark.gpu]
EVIDENCE = Path(__file__).resolve().parents[2] / "benchmarks" / "results" / "gate5"
SRC = HOME / "data" / "variants" / "terrace" / "clean"


def test_dense_job_end_to_end() -> None:
    if (
        not SRC.is_dir()
        or not (HOME / "tools" / "colmap-cuda" / "bin" / "colmap.exe").exists()
    ):
        pytest.skip("variants or CUDA COLMAP not installed")
    files = sorted(p for p in SRC.iterdir() if p.suffix.lower() == ".jpg")
    t = Tenant()
    _p, d = t.make_dataset(files)
    r = t.call(
        "POST",
        f"/v1/datasets/{d}/jobs",
        json={"label": "dense", "config": {"dense": True}},
    )
    assert r.status_code == 202
    job_id = r.json()["id"]
    t0 = time.perf_counter()
    job, seen = t.wait(job_id, TERMINAL, timeout=2400)
    wall = time.perf_counter() - t0
    assert job["state"] == "COMPLETED", (job.get("diagnostic"), seen)
    assert not [w for w in job["warnings"] if w["code"].startswith("DENSE")], job[
        "warnings"
    ]

    dense = job["metrics"]["dense"]
    assert dense["n_points"] > 50_000
    assert 0 < dense["peak_vram_mib"] < 8188
    assert {"dense_patch_match", "dense_fusion"} <= {s["name"] for s in job["stages"]}
    kinds = {a["kind"] for a in t.call("GET", f"/v1/jobs/{job_id}/artifacts").json()}
    assert {"points_dense_ply", "points_dense_lod", "points_ply"} <= kinds

    scene = t.call("GET", f"/v1/jobs/{job_id}/scene").json()
    assert scene["densePointsUrl"] and scene["densePointCount"] == dense["n_points"]
    lod = httpx.get(scene["densePointsUrl"], timeout=120).content
    magic, version, count, levels = struct.unpack("<4sIII", lod[:16])
    # The browser file keeps every dense point (nothing is decimated; levels only reorder them).
    assert magic == b"PXLD" and version == 1 and count == dense["n_points"]
    ends = struct.unpack_from(f"<{levels}I", lod, 40)
    assert list(ends) == sorted(set(ends)) and ends[-1] == count and levels > 3
    assert len(lod) == -(-(40 + 4 * levels) // 16) * 16 + count * 16

    # Not evidence: lets benchmarks/measure_viewer.py open this exact job in a browser afterwards.
    scratch = HOME / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "last_dense_job.json").write_text(
        json.dumps({"tenant": t.id, "job": job_id}), encoding="utf-8"
    )

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "dense_job.json").write_text(
        json.dumps(
            {
                "wall_seconds": round(wall, 1),
                "timeline": seen,
                "sparse_points": job["metrics"]["n_points3d"],
                "dense": dense,
                "browser_points": count,
                "browser_levels": list(ends),
                "browser_file_bytes": len(lod),
                "stages": {s["name"]: s["seconds"] for s in job["stages"]},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
