"""Copy accepted images into an isolated per-attempt workspace with safe, ordered names."""

from __future__ import annotations

import shutil
from pathlib import Path

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
