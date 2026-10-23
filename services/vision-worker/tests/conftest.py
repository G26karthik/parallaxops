from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def textured_image(seed: int, size: tuple[int, int] = (320, 240)) -> Image.Image:
    """Deterministic high-frequency texture. Different seeds give visually unrelated images."""
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


@pytest.fixture
def image_dir(tmp_path: Path) -> Path:
    d = tmp_path / "imgs"
    d.mkdir()
    for i in range(6):
        textured_image(i).save(d / f"photo_{i:02d}.jpg", quality=92)
    return d


def dataset_root() -> Path | None:
    home = os.environ.get("PARALLAXOPS_HOME")
    if not home:
        return None
    p = Path(home) / "data" / "eth3d" / "terrace" / "terrace"
    return p if (p / "images" / "dslr_images").is_dir() else None


@pytest.fixture(scope="session")
def terrace() -> Path:
    root = dataset_root()
    if root is None:
        pytest.skip("ETH3D terrace not downloaded (see docs/DATASETS.md)")
    return root
