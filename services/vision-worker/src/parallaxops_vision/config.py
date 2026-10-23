"""Versioned reconstruction configuration. The same object is stored in manifest.json."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field, model_validator

CONFIG_SCHEMA_VERSION = 1

MatchingPolicy = Literal["auto", "exhaustive", "sequential", "spatial"]
MapperKind = Literal["incremental", "global"]
CameraModel = Literal["SIMPLE_RADIAL", "RADIAL", "OPENCV", "SIMPLE_PINHOLE", "PINHOLE"]


class Budgets(BaseModel):
    """Hard resource limits enforced by the supervisor and by input validation."""

    min_images: int = 5
    max_images: int = 80
    max_total_bytes: int = 1_500_000_000
    max_pixels_per_image: int = 60_000_000
    max_total_megapixels: int = 2_500
    wall_clock_seconds: int = 3_600
    max_rss_bytes: int = 14 * 1024**3
    max_workspace_bytes: int = 8 * 1024**3


class QCThresholds(BaseModel):
    """Heuristic thresholds. They flag capture risk, they do not measure scene coverage."""

    blur_laplacian_var_min: float = 40.0
    dark_mean_luma_max: float = 35.0
    overexposed_fraction_max: float = 0.12
    dedup_hamming_max: int = 3
    min_registered_fraction_ok: float = 0.6


class ReconstructionConfig(BaseModel):
    schema_version: int = CONFIG_SCHEMA_VERSION
    seed: int = 0
    max_image_size: int = Field(1600, ge=256, le=8192)
    max_num_features: int = Field(8192, ge=512, le=32768)
    camera_model: CameraModel = "SIMPLE_RADIAL"
    single_camera: bool = True
    matching: MatchingPolicy = "auto"
    sequential_overlap: int = Field(10, ge=1, le=50)
    mapper: MapperKind = "incremental"
    min_num_matches: int = 15
    min_model_size: int = 3
    num_threads: int = Field(default_factory=lambda: max(1, min(8, (os.cpu_count() or 4) // 2)))
    exclude_images: list[str] = Field(default_factory=list)
    budgets: Budgets = Field(default_factory=Budgets)
    qc: QCThresholds = Field(default_factory=QCThresholds)

    @model_validator(mode="after")
    def _check(self) -> ReconstructionConfig:
        if self.budgets.min_images > self.budgets.max_images:
            raise ValueError("min_images exceeds max_images")
        return self
