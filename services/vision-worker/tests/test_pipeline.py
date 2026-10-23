from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import textured_image
from typer.testing import CliRunner

from parallaxops_vision.cli import app
from parallaxops_vision.config import Budgets, ReconstructionConfig
from parallaxops_vision.diagnostics import EXIT_CODES, DiagnosticCode
from parallaxops_vision.export import publish_atomic
from parallaxops_vision.pipeline import reconstruct


def test_bad_set_publishes_typed_failure_and_leaves_no_staging(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    for i in range(2):
        textured_image(i).save(src / f"{i}.jpg")
    out = tmp_path / "out"
    rep = reconstruct(src, out, ReconstructionConfig())
    assert rep["status"] == "failed"
    assert rep["diagnostic"]["code"] == DiagnosticCode.TOO_FEW_IMAGES
    assert rep["diagnostic"]["advice"]
    assert (out / "report.json").exists() and (out / "manifest.json").exists()
    assert not [p for p in tmp_path.iterdir() if ".staging-" in p.name]


def test_noise_images_fail_with_typed_diagnostic_not_crash(tmp_path: Path) -> None:
    """Unrelated random noise has no matchable structure: must end in a typed diagnostic."""
    src = tmp_path / "in"
    src.mkdir()
    for i in range(6):
        textured_image(i, (480, 360)).save(src / f"{i}.jpg", quality=95)
    cfg = ReconstructionConfig(
        max_image_size=480, budgets=Budgets(min_images=5, wall_clock_seconds=120)
    )
    rep = reconstruct(src, tmp_path / "out", cfg, render=False)
    assert rep["status"] == "failed"
    assert rep["diagnostic"]["code"] in {
        DiagnosticCode.NO_FEATURES,
        DiagnosticCode.INSUFFICIENT_MATCHES,
        DiagnosticCode.SFM_NO_MODEL,
        DiagnosticCode.NO_INITIAL_PAIR,
        DiagnosticCode.LOW_REGISTRATION,
    }
    assert (tmp_path / "out" / "logs").is_dir()  # trace preserved


def test_cli_exit_code_is_typed_on_bad_input(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    textured_image(1).save(src / "a.jpg")
    res = CliRunner().invoke(app, ["reconstruct", "-i", str(src), "-o", str(tmp_path / "o")])
    assert res.exit_code == EXIT_CODES[DiagnosticCode.TOO_FEW_IMAGES]


def test_publish_atomic_refuses_clobber_without_force(tmp_path: Path) -> None:
    final = tmp_path / "final"
    final.mkdir()
    (final / "old.txt").write_text("old")
    staging = tmp_path / "stg"
    staging.mkdir()
    (staging / "new.txt").write_text("new")
    with pytest.raises(FileExistsError):
        publish_atomic(staging, final, overwrite=False)
    assert (final / "old.txt").exists() and staging.exists()
    publish_atomic(staging, final, overwrite=True)
    assert (final / "new.txt").exists() and not (final / "old.txt").exists()
    assert not [p for p in tmp_path.iterdir() if ".old-" in p.name]


@pytest.mark.integration
def test_real_reconstruction_terrace(terrace: Path, tmp_path: Path) -> None:
    """Real ETH3D multi-view images through the real COLMAP engine, scored against GT poses."""
    out = tmp_path / "terrace"
    rep = reconstruct(
        terrace / "images" / "dslr_images",
        out,
        ReconstructionConfig(),
        gt_images_txt=terrace / "dslr_calibration_jpg" / "images.txt",
        render=False,
    )
    assert rep["status"] == "completed", rep.get("diagnostic")
    m = rep["metrics"]
    assert m["n_registered"] > 1 and m["n_registered"] >= 0.9 * m["n_input_images"]
    assert m["n_points3d"] > 1000
    assert m["reproj_error_px_mean"] < 2.0

    # PLY is real and non-empty; manifest checksums match what is on disk.
    import open3d as o3d

    assert len(o3d.io.read_point_cloud(str(out / "points_sparse.ply")).points) == m["n_points3d"]
    manifest = json.loads((out / "manifest.json").read_text())
    for a in manifest["artifacts"]:
        assert hashlib.sha256((out / a["path"]).read_bytes()).hexdigest() == a["sha256"]
    assert manifest["tools"]["pycolmap"] and manifest["config"]["seed"] == 0

    gt = rep["ground_truth_eval"]
    assert gt["n_inliers"] >= 0.9 * gt["n_matched_to_gt"]
    assert gt["ate_rmse_all_over_spread"] < 0.05  # camera positions within 5% of rig spread
    assert gt["rotation_error_deg_median"] < 3.0
