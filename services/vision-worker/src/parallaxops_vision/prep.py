"""Copy accepted images into an isolated per-attempt workspace with safe, ordered names."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel

from .config import ReconstructionConfig
from .qc import QCReport

_EXIF_ORIENTATION = 0x0112


class PreparedImage(BaseModel):
    safe_name: str
    original_name: str
    sha256: str
    original_size: tuple[int, int]
    prepared_size: tuple[int, int]
    scale: float


def prepare_images(
    input_dir: Path, qc: QCReport, cfg: ReconstructionConfig, dest: Path
) -> list[PreparedImage]:
    """Write accepted images to dest as img_0001.jpg ... keeping the original sort order.

    User-supplied filenames never reach COLMAP or the filesystem layout; only img_NNNN names do.
    """
    dest.mkdir(parents=True, exist_ok=True)
    root = input_dir.resolve()
    excluded = set(cfg.exclude_images)
    keep = [i for i in qc.images if "exact_duplicate" not in i.flags and i.name not in excluded]
    out: list[PreparedImage] = []
    for idx, im in enumerate(keep, start=1):
        src = root / im.name
        safe = f"img_{idx:04d}.jpg"
        with Image.open(src) as img:
            exif = img.getexif()
            oriented = ImageOps.exif_transpose(img)
            ow, oh = oriented.size
            scale = min(1.0, cfg.max_image_size / max(ow, oh))
            needs_encode = (
                scale < 1.0 or src.suffix.lower() == ".png" or exif.get(_EXIF_ORIENTATION, 1) != 1
            )
            if not needs_encode:
                shutil.copyfile(src, dest / safe)
                size = (ow, oh)
            else:
                if scale < 1.0:
                    size = (round(ow * scale), round(oh * scale))
                    oriented = oriented.resize(size, Image.Resampling.LANCZOS)
                else:
                    size = (ow, oh)
                exif[_EXIF_ORIENTATION] = 1
                oriented.convert("RGB").save(dest / safe, "JPEG", quality=95, exif=exif.tobytes())
        out.append(
            PreparedImage(
                safe_name=safe,
                original_name=im.name,
                sha256=im.sha256,
                original_size=(ow, oh),
                prepared_size=size,
                scale=round(size[0] / ow, 6),
            )
        )
    return out


def prepare_masks(masks_dir: Path, prepared: list[PreparedImage], dest: Path) -> dict[str, float]:
    """Match user masks to the prepared images and write them at the prepared size.

    A mask for DSC_0001.JPG is DSC_0001.png in masks_dir: grayscale, 0 = ignore this pixel when
    extracting features, anything else = keep. COLMAP wants <prepared name>.png at the prepared
    image size. Images without a mask are used whole. Returns summary numbers for the report.
    """
    dest.mkdir(parents=True, exist_ok=True)
    root = masks_dir.resolve()
    ignored: list[float] = []
    for p in prepared:
        src = root / f"{Path(p.original_name).stem}.png"
        if not src.is_file():
            continue
        with Image.open(src) as m:
            keep = (
                ImageOps.exif_transpose(m)
                .convert("L")
                .resize(p.prepared_size, Image.Resampling.NEAREST)
            )
        used = np.asarray(keep) > 0
        Image.fromarray(used.astype("u1") * 255).save(dest / f"{p.safe_name}.png")
        ignored.append(1 - float(used.mean()))
    return {
        "n_masks": len(ignored),
        "n_images": len(prepared),
        "mean_ignored_fraction": round(sum(ignored) / len(ignored), 4) if ignored else 0.0,
        "max_ignored_fraction": round(max(ignored), 4) if ignored else 0.0,
    }
