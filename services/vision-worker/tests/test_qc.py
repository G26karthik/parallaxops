from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from conftest import textured_image
from PIL import Image

from parallaxops_vision.config import Budgets, ReconstructionConfig
from parallaxops_vision.diagnostics import DiagnosticCode, ReconstructionError
from parallaxops_vision.qc import run_qc


def test_clean_set_passes(image_dir: Path) -> None:
    cfg = ReconstructionConfig(budgets=Budgets(min_images=5))
    rep = run_qc(image_dir, cfg)
    assert len(rep.images) == 6
    assert not rep.rejected
    assert all(len(i.sha256) == 64 for i in rep.images)


def test_too_few_images_is_typed(image_dir: Path) -> None:
    cfg = ReconstructionConfig(budgets=Budgets(min_images=10))
    with pytest.raises(ReconstructionError) as e:
        run_qc(image_dir, cfg)
    assert e.value.diagnostic.code is DiagnosticCode.TOO_FEW_IMAGES
    assert "15" in e.value.diagnostic.advice


def test_exact_duplicate_flagged_and_excluded(image_dir: Path) -> None:
    (image_dir / "copy.jpg").write_bytes((image_dir / "photo_00.jpg").read_bytes())
    rep = run_qc(image_dir, ReconstructionConfig())
    pair = {"copy.jpg", "photo_00.jpg"}
    flagged = {i.name for i in rep.images if "exact_duplicate" in i.flags}
    assert len(flagged) == 1 and flagged < pair  # first in sorted order is kept
    assert rep.duplicate_groups == [sorted(pair)]


def test_blur_dark_and_overexposed_flags(tmp_path: Path) -> None:
    d = tmp_path / "x"
    d.mkdir()
    sharp = np.asarray(textured_image(1))
    cv2.imwrite(str(d / "sharp.jpg"), cv2.cvtColor(sharp, cv2.COLOR_RGB2BGR))
    blurred = cv2.GaussianBlur(sharp, (0, 0), 6)
    cv2.imwrite(str(d / "blurry.jpg"), cv2.cvtColor(blurred, cv2.COLOR_RGB2BGR))
    Image.fromarray(np.full((240, 320, 3), 8, np.uint8)).save(d / "dark.jpg")
    Image.fromarray(np.full((240, 320, 3), 255, np.uint8)).save(d / "bright.jpg")
    rep = run_qc(d, ReconstructionConfig(budgets=Budgets(min_images=1)))
    flags = {i.name: set(i.flags) for i in rep.images}
    assert "possibly_blurry" not in flags["sharp.jpg"]
    assert "possibly_blurry" in flags["blurry.jpg"]
    assert "dark" in flags["dark.jpg"]
    assert "overexposed" in flags["bright.jpg"]


def test_non_image_and_corrupt_files_are_rejected_not_fatal(image_dir: Path) -> None:
    (image_dir / "notes.txt").write_text("hello")
    (image_dir / "broken.jpg").write_bytes(b"not a jpeg at all")
    rep = run_qc(image_dir, ReconstructionConfig())
    reasons = {r.name: r.reason for r in rep.rejected}
    assert "notes.txt" in reasons
    assert reasons["broken.jpg"].startswith("undecodable")
    assert len(rep.images) == 6


def test_pixel_budget_rejects_oversized_image(tmp_path: Path) -> None:
    d = tmp_path / "big"
    d.mkdir()
    textured_image(0, (400, 300)).save(d / "a.jpg")
    cfg = ReconstructionConfig(budgets=Budgets(min_images=1, max_pixels_per_image=1000))
    with pytest.raises(ReconstructionError) as e:
        run_qc(d, cfg)
    assert e.value.diagnostic.code is DiagnosticCode.UNREADABLE_IMAGES


def test_total_bytes_budget(image_dir: Path) -> None:
    cfg = ReconstructionConfig(budgets=Budgets(min_images=1, max_total_bytes=1000))
    with pytest.raises(ReconstructionError) as e:
        run_qc(image_dir, cfg)
    assert e.value.diagnostic.code is DiagnosticCode.INPUT_TOO_LARGE


def test_symlink_escaping_input_dir_is_rejected(image_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.jpg"
    textured_image(99).save(outside)
    link = image_dir / "evil.jpg"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need privileges on this host")
    rep = run_qc(image_dir, ReconstructionConfig())
    assert any(r.name == "evil.jpg" and "escapes" in r.reason for r in rep.rejected)
    assert all(i.name != "evil.jpg" for i in rep.images)
