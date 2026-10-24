"""User masks: they reach COLMAP at the prepared size, and masked pixels yield no features."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pycolmap
from PIL import Image, ImageDraw

from parallaxops_vision.prep import PreparedImage, prepare_masks


def prepared(name: str, safe: str, size: tuple[int, int]) -> PreparedImage:
    return PreparedImage(
        safe_name=safe,
        original_name=name,
        sha256="0" * 64,
        original_size=size,
        prepared_size=size,
        scale=1.0,
    )


def test_masks_are_matched_by_name_and_written_at_prepared_size(tmp_path: Path) -> None:
    masks = tmp_path / "user_masks"
    masks.mkdir()
    keep = np.full((240, 320), 255, dtype="u1")
    keep[:, :160] = 0  # left half ignored, and a soft edge value that must become 255
    keep[0, 200] = 7
    Image.fromarray(keep).save(masks / "DSC_0001.png")
    out = tmp_path / "work_masks"
    summary = prepare_masks(
        masks,
        [
            prepared("sub/DSC_0001.JPG", "img_0001.jpg", (160, 120)),
            prepared("b.jpg", "img_0002.jpg", (160, 120)),
        ],
        out,
    )
    got = np.asarray(Image.open(out / "img_0001.jpg.png"))
    assert got.shape == (120, 160) and set(np.unique(got)) == {0, 255}
    assert (got[:, :80] == 0).all() and (got[:, 81:] == 255).all()
    assert not (out / "img_0002.jpg.png").exists()  # no mask: that image is used whole
    assert summary["n_masks"] == 1 and summary["n_images"] == 2
    assert abs(summary["mean_ignored_fraction"] - 0.5) < 0.01


def test_colmap_extracts_no_features_where_the_mask_is_zero(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    im = Image.new("L", (320, 240), 128)
    draw = ImageDraw.Draw(im)
    for _ in range(400):
        x, y = int(rng.integers(0, 300)), int(rng.integers(0, 220))
        draw.rectangle(
            [x, y, x + int(rng.integers(4, 20)), y + int(rng.integers(4, 20))],
            fill=int(rng.integers(0, 255)),
        )
    images = tmp_path / "images"
    images.mkdir()
    im.convert("RGB").save(images / "img_0001.jpg", quality=95)
    mask = np.full((240, 320), 255, dtype="u1")
    mask[:, 160:] = 0  # right half ignored
    masks = tmp_path / "masks"
    masks.mkdir()
    Image.fromarray(mask).save(masks / "img_0001.jpg.png")

    def keypoint_x(db: Path, mask_dir: Path | None) -> np.ndarray:
        reader = pycolmap.ImageReaderOptions()
        if mask_dir is not None:
            reader.mask_path = str(mask_dir)
        fx = pycolmap.FeatureExtractionOptions()
        fx.use_gpu = False
        pycolmap.extract_features(
            db, images, reader_options=reader, extraction_options=fx, device=pycolmap.Device.cpu
        )
        con = sqlite3.connect(db)
        try:
            rows, cols, blob = con.execute("SELECT rows, cols, data FROM keypoints").fetchone()
        finally:
            con.close()
        return np.frombuffer(blob, dtype="<f4").reshape(rows, cols)[:, 0]

    free = keypoint_x(tmp_path / "free.db", None)
    masked = keypoint_x(tmp_path / "masked.db", masks)
    assert (free > 160).sum() > 20, "control: the unmasked image has features on the right"
    assert len(masked) > 20 and (masked < 161).all()
