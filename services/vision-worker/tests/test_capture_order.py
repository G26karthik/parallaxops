"""Sequential matching (more than 60 images) assumes sorted filenames follow capture order."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from conftest import textured_image
from PIL import Image

from parallaxops_vision.config import Budgets, ReconstructionConfig
from parallaxops_vision.qc import order_ratio, run_qc

CFG = ReconstructionConfig(budgets=Budgets(max_images=100))
WARNING = "filenames do not look like capture order"


def chain(n: int) -> list[str]:
    """Hashes that change by one bit per step: neighbours are far more alike than random pairs."""
    return [f"{(1 << i) - 1:016x}" for i in range(n)]


def test_ratio_is_low_for_a_capture_sequence_and_about_one_when_shuffled() -> None:
    ordered = chain(64)
    shuffled = ordered[:]
    random.Random(0).shuffle(shuffled)
    assert order_ratio(ordered) < 0.2
    assert 0.8 < order_ratio(shuffled) < 1.2


def sinusoid(phase: float, size: tuple[int, int] = (160, 120)) -> Image.Image:
    x = np.arange(size[0]) / size[0]
    row = 127 + 120 * np.sin(2 * np.pi * (3 * x + phase))
    return Image.fromarray(np.tile(row, (size[1], 1)).astype(np.uint8)).convert("RGB")


def test_ordered_set_over_60_images_gets_no_warning(tmp_path: Path) -> None:
    for i in range(62):
        sinusoid(i / 62).save(tmp_path / f"{i:03d}.jpg", quality=95)
    assert not [w for w in run_qc(tmp_path, CFG).warnings if WARNING in w]


def test_unrelated_images_over_60_are_warned_about(tmp_path: Path) -> None:
    for i in range(62):
        textured_image(i, (160, 120)).save(tmp_path / f"{i:03d}.jpg", quality=95)
    assert [w for w in run_qc(tmp_path, CFG).warnings if WARNING in w]


def test_no_warning_when_every_pair_is_matched_anyway(tmp_path: Path) -> None:
    for i in range(30):  # at most 60 images: exhaustive matching does not use the order
        textured_image(i, (160, 120)).save(tmp_path / f"{i:03d}.jpg", quality=95)
    assert not [w for w in run_qc(tmp_path, CFG).warnings if WARNING in w]
