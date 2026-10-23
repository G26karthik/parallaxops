from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import open3d as o3d
import pytest

from parallaxops_vision import dense
from parallaxops_vision.config import ReconstructionConfig
from parallaxops_vision.diagnostics import DiagnosticCode, ReconstructionError
from parallaxops_vision.pipeline import reconstruct
from parallaxops_vision.supervisor import SupervisedResult

FAKE_EXE = Path("C:/fake/colmap.exe")


def result(code: int | None = 0, killed: str | None = None) -> SupervisedResult:
    return SupervisedResult(
        returncode=code, killed_reason=killed, wall_seconds=1, peak_rss_bytes=1, cpu_seconds=1
    )


def write_ply(path: Path, n: int = 50) -> None:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.random.default_rng(0).normal(size=(n, 3)))
    o3d.io.write_point_cloud(path, pcd)


class FakeSupervisor:
    """Stands in for run_supervised: records argv and 'produces' the fused cloud."""

    def __init__(
        self, ply_points: int = 50, fail_stage: str | None = None, killed: str | None = None
    ):
        self.calls: list[list[str]] = []
        self.ply_points, self.fail_stage, self.killed = ply_points, fail_stage, killed

    def __call__(self, argv: list[str], **kw: Any) -> SupervisedResult:
        self.calls.append(list(argv))
        name = argv[1]
        if name == "stereo_fusion" and self.ply_points:
            write_ply(Path(argv[argv.index("--output_path") + 1]), self.ply_points)
        if name == self.fail_stage:
            return result(code=1) if not self.killed else result(code=None, killed=self.killed)
        return result()


@pytest.fixture
def gpu_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dense, "gpu_memory_mib", lambda: (500, 8188))


def run(
    tmp_path: Path, fake: FakeSupervisor, monkeypatch: pytest.MonkeyPatch, **kw: Any
) -> dict[str, Any]:
    monkeypatch.setattr(dense, "run_supervised", fake)
    monkeypatch.setattr(
        dense.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": "COLMAP 4.2.1 (test)\n"})(),
    )
    return dense.run_dense(
        tmp_path / "sparse",
        tmp_path / "images",
        tmp_path / "work",
        tmp_path / "fused.ply",
        colmap=FAKE_EXE,
        **kw,
    )


def test_pipeline_order_flags_and_argv_safety(
    tmp_path: Path, gpu_ok: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSupervisor()
    rep = run(tmp_path, fake, monkeypatch, max_image_size=800)
    assert [c[1] for c in fake.calls] == [
        "image_undistorter",
        "patch_match_stereo",
        "stereo_fusion",
    ]
    assert all(c[0] == str(FAKE_EXE) and not c[0].lower().endswith(".bat") for c in fake.calls)
    assert all(isinstance(a, str) for c in fake.calls for a in c)  # argv list, never a shell string
    pm = fake.calls[1]
    assert pm[pm.index("--PatchMatchStereo.max_image_size") + 1] == "800"
    assert pm[pm.index("--PatchMatchStereo.geom_consistency") + 1] == "true"
    assert fake.calls[2][fake.calls[2].index("--input_type") + 1] == "geometric"
    assert rep["n_points"] == 50 and rep["colmap"].startswith("COLMAP 4.2.1")
    assert set(rep["stages_seconds"]) == {"dense_undistort", "dense_patch_match", "dense_fusion"}


def test_photometric_mode_when_geometric_consistency_is_off(
    tmp_path: Path, gpu_ok: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSupervisor()
    run(tmp_path, fake, monkeypatch, geom_consistency=False)
    assert fake.calls[2][fake.calls[2].index("--input_type") + 1] == "photometric"


def test_failures_are_typed(tmp_path: Path, gpu_ok: None, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ReconstructionError) as e:
        run(tmp_path, FakeSupervisor(fail_stage="patch_match_stereo"), monkeypatch)
    assert e.value.diagnostic.code is DiagnosticCode.DENSE_FAILED
    with pytest.raises(ReconstructionError) as e2:
        run(
            tmp_path, FakeSupervisor(fail_stage="patch_match_stereo", killed="timeout"), monkeypatch
        )
    assert e2.value.diagnostic.code is DiagnosticCode.TIMEOUT
    with pytest.raises(ReconstructionError) as e3:
        run(
            tmp_path,
            FakeSupervisor(fail_stage="image_undistorter", killed="cancelled"),
            monkeypatch,
        )
    assert e3.value.diagnostic.code is DiagnosticCode.CANCELLED
    with pytest.raises(ReconstructionError) as e4:
        run(tmp_path, FakeSupervisor(ply_points=0), monkeypatch)  # fusion writes nothing
    assert e4.value.diagnostic.code is DiagnosticCode.DENSE_FAILED


def test_capability_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ReconstructionError) as e:
        dense.check_capability(None)
    assert (
        e.value.diagnostic.code is DiagnosticCode.DENSE_UNAVAILABLE
        and "CUDA build" in e.value.diagnostic.message
    )

    monkeypatch.setattr(dense, "gpu_memory_mib", lambda: None)
    with pytest.raises(ReconstructionError) as e2:
        dense.check_capability(FAKE_EXE)
    assert "No NVIDIA GPU" in e2.value.diagnostic.message

    monkeypatch.setattr(dense, "gpu_memory_mib", lambda: (7500, 8188))  # 688 MiB free
    with pytest.raises(ReconstructionError) as e3:
        dense.check_capability(FAKE_EXE)
    assert "688 MiB" in e3.value.diagnostic.message and e3.value.diagnostic.advice

    monkeypatch.setattr(dense, "gpu_memory_mib", lambda: (500, 8188))
    dense.check_capability(FAKE_EXE)  # enough free memory: no error


def test_finder_honours_the_override_and_ignores_missing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "colmap.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("COLMAP_CUDA_EXE", str(exe))
    assert dense.find_colmap_cuda() == exe
    monkeypatch.setenv("COLMAP_CUDA_EXE", str(tmp_path / "nope.exe"))
    monkeypatch.setenv("PARALLAXOPS_HOME", str(tmp_path / "empty"))
    assert dense.find_colmap_cuda() is None


@pytest.mark.integration
def test_dense_unavailable_keeps_the_sparse_result(
    terrace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real SfM; dense requested but no CUDA COLMAP: the job still completes with a typed warning."""
    monkeypatch.delenv("COLMAP_CUDA_EXE", raising=False)
    monkeypatch.setenv("PARALLAXOPS_HOME", str(tmp_path / "no-tools"))
    rep = reconstruct(
        terrace / "images" / "dslr_images",
        tmp_path / "out",
        ReconstructionConfig(dense=True),
        render=False,
    )
    assert rep["status"] == "completed" and rep["metrics"]["n_registered"] > 1
    assert "dense" not in rep
    codes = [w["code"] for w in rep["warnings"]]
    assert "DENSE_UNAVAILABLE" in codes
    assert not (tmp_path / "out" / "points_dense.ply").exists()
