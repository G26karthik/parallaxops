"""Input validation and image quality control.

Blur, exposure and duplicate checks are cheap *heuristics* that flag capture risk. They do not
measure scene coverage or guarantee reconstructability; the SfM result is the only real test.
"""

from __future__ import annotations

import hashlib
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .config import ReconstructionConfig
from .diagnostics import Diagnostic, DiagnosticCode, ReconstructionError

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png"}
_EXIF_IFD = 0x8769
_TAG_FOCAL = 0x920A
_TAG_FOCAL35 = 0xA405
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_ANALYSIS_LONG_SIDE = 1024
_UNORDERED_RATIO = 0.9  # between the slowest ordered capture measured (0.83) and shuffled (0.93+)


class ImageQC(BaseModel):
    name: str
    sha256: str
    bytes: int
    width: int
    height: int
    exif_focal_mm: float | None = None
    exif_focal_35mm: float | None = None
    camera: str | None = None
    laplacian_var: float
    mean_luma: float
    overexposed_fraction: float
    dhash: str
    flags: list[str] = Field(default_factory=list)


class RejectedImage(BaseModel):
    name: str
    reason: str


class QCReport(BaseModel):
    note: str = (
        "Heuristic capture checks only. Laplacian variance, luma and hash distance do not prove "
        "overlap, texture sufficiency or reconstructability."
    )
    images: list[ImageQC]
    rejected: list[RejectedImage]
    duplicate_groups: list[list[str]]
    warnings: list[str]
    total_bytes: int
    total_megapixels: float


def list_input_images(input_dir: Path) -> tuple[list[Path], list[RejectedImage]]:
    """List regular image files directly under input_dir. Never follows links out of the tree."""
    root = input_dir.resolve()
    if not root.is_dir():
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.UNREADABLE_IMAGES, f"Input is not a directory: {input_dir.name}"
            )
        )
    files: list[Path] = []
    rejected: list[RejectedImage] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() not in ALLOWED_SUFFIXES:
            rejected.append(RejectedImage(name=rel, reason="unsupported file type"))
            continue
        if not p.resolve().is_relative_to(root):
            rejected.append(RejectedImage(name=rel, reason="path escapes input directory"))
            continue
        files.append(p)
    return files, rejected


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dhash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def order_ratio(dhashes: list[str]) -> float:
    """Mean hash distance between neighbours in name order, over the mean between all pairs.

    Near 1 means neighbours look no more alike than random pairs, so the names do not follow
    capture order. Measured on real captures: ETH3D terrace in order 0.83, TUM walking_xyz in
    order 0.54; the same sets shuffled 20 times gave 0.93 to 1.08.
    """
    adjacent = [hamming(a, b) for a, b in pairwise(dhashes)]
    pairs = [hamming(a, b) for i, a in enumerate(dhashes) for b in dhashes[i + 1 :]]
    mean_all = sum(pairs) / len(pairs)
    return (sum(adjacent) / len(adjacent)) / mean_all if mean_all else 1.0


def _exif_numbers(img: Image.Image) -> tuple[float | None, float | None, str | None]:
    try:
        exif = img.getexif()
        sub = exif.get_ifd(_EXIF_IFD)
        focal = sub.get(_TAG_FOCAL)
        focal35 = sub.get(_TAG_FOCAL35)
        make = str(exif.get(_TAG_MAKE, "")).strip()
        model = str(exif.get(_TAG_MODEL, "")).strip()
        cam = f"{make} {model}".strip() or None
        return (
            float(focal) if focal else None,
            float(focal35) if focal35 else None,
            cam,
        )
    except Exception:
        return None, None, None


def analyze_image(path: Path, rel_name: str, cfg: ReconstructionConfig) -> ImageQC:
    size_bytes = path.stat().st_size
    with Image.open(path) as probe:
        w, h = probe.size
        if w * h > cfg.budgets.max_pixels_per_image:
            raise ValueError(f"{w}x{h} exceeds pixel budget")
        if probe.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # orientation swaps axes
            w, h = h, w
    with Image.open(path) as img:
        focal, focal35, cam = _exif_numbers(img)
        img.draft("L", (_ANALYSIS_LONG_SIDE * 2, _ANALYSIS_LONG_SIDE * 2))  # fast JPEG downscale
        gray_img = ImageOps.exif_transpose(img).convert("L")
    gray = np.asarray(gray_img)
    scale = _ANALYSIS_LONG_SIDE / max(gray.shape)
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    luma = float(gray.mean())
    over = float((gray >= 250).mean())

    flags: list[str] = []
    t = cfg.qc
    if lap < t.blur_laplacian_var_min:
        flags.append("possibly_blurry")
    if luma < t.dark_mean_luma_max:
        flags.append("dark")
    if over > t.overexposed_fraction_max:
        flags.append("overexposed")
    return ImageQC(
        name=rel_name,
        sha256=_sha256(path),
        bytes=size_bytes,
        width=w,
        height=h,
        exif_focal_mm=focal,
        exif_focal_35mm=focal35,
        camera=cam,
        laplacian_var=round(lap, 3),
        mean_luma=round(luma, 3),
        overexposed_fraction=round(over, 5),
        dhash=_dhash(gray),
        flags=flags,
    )


def run_qc(input_dir: Path, cfg: ReconstructionConfig) -> QCReport:
    files, rejected = list_input_images(input_dir)
    root = input_dir.resolve()
    b = cfg.budgets
    total_bytes = sum(p.stat().st_size for p in files)
    if total_bytes > b.max_total_bytes:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.INPUT_TOO_LARGE,
                f"Total input {total_bytes} B exceeds budget {b.max_total_bytes} B",
                total_bytes=total_bytes,
                budget=b.max_total_bytes,
            )
        )
    if len(files) > b.max_images:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.TOO_MANY_IMAGES,
                f"{len(files)} images exceeds the budget of {b.max_images}",
                count=len(files),
            )
        )

    images: list[ImageQC] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            images.append(analyze_image(p, rel, cfg))
        except Exception as exc:
            rejected.append(
                RejectedImage(name=rel, reason=f"undecodable: {type(exc).__name__}: {exc}")
            )

    # Exact duplicates by content hash; keep the first, flag the rest.
    seen: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for im in images:
        if im.sha256 in seen:
            im.flags.append("exact_duplicate")
            groups.setdefault(seen[im.sha256], [seen[im.sha256]]).append(im.name)
        else:
            seen[im.sha256] = im.name

    # Near duplicates by perceptual hash among unique images (informational, not removed).
    uniq = [i for i in images if "exact_duplicate" not in i.flags]
    for i, a in enumerate(uniq):
        for c in uniq[i + 1 :]:
            if hamming(a.dhash, c.dhash) <= cfg.qc.dedup_hamming_max:
                for im in (a, c):
                    if "near_duplicate" not in im.flags:
                        im.flags.append("near_duplicate")

    n_ok = len(uniq)
    mp = sum(i.width * i.height for i in images) / 1e6
    if mp > b.max_total_megapixels:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.INPUT_TOO_LARGE,
                f"Total {mp:.0f} MP exceeds budget {b.max_total_megapixels} MP",
                megapixels=round(mp, 1),
            )
        )
    if n_ok < b.min_images:
        code = DiagnosticCode.UNREADABLE_IMAGES if not images else DiagnosticCode.TOO_FEW_IMAGES
        raise ReconstructionError(
            Diagnostic.of(
                code,
                f"Only {n_ok} usable unique images (need >= {b.min_images}); "
                f"{len(rejected)} rejected, {len(images) - n_ok} exact duplicates",
                usable=n_ok,
                rejected=[r.model_dump() for r in rejected][:20],
            )
        )

    warnings: list[str] = []
    if len({(i.width, i.height) for i in images}) > 1:
        warnings.append("Mixed image resolutions; a single shared camera may be inappropriate.")
    if not any(i.exif_focal_mm or i.exif_focal_35mm for i in images):
        warnings.append("No EXIF focal length found; intrinsics will be estimated from the images.")
    blurry = [i.name for i in images if "possibly_blurry" in i.flags]
    if blurry and len(blurry) > 0.2 * len(images):
        warnings.append(
            f"{len(blurry)}/{len(images)} images are flagged possibly blurry (heuristic)."
        )
    if groups:
        warnings.append(
            f"{sum(len(g) - 1 for g in groups.values())} exact duplicate images ignored."
        )
    if n_ok < 15:
        warnings.append("Fewer than 15 images: reconstructions are fragile; 15-35 is recommended.")
    # Above 60 images matching is sequential: each image is matched only with its neighbours in
    # name order. If the names were shuffled, most true overlaps are never tried.
    if len(images) > 60 and cfg.matching in ("auto", "sequential"):
        ratio = order_ratio([i.dhash for i in images])
        if ratio > _UNORDERED_RATIO:
            warnings.append(
                f"{len(images)} images are matched in filename order, but the filenames do not "
                f"look like capture order (neighbour similarity ratio {ratio:.2f}). Rename the "
                "files so they sort in the order they were taken, or expect missing overlaps."
            )
    return QCReport(
        images=images,
        rejected=rejected,
        duplicate_groups=list(groups.values()),
        warnings=warnings,
        total_bytes=total_bytes,
        total_megapixels=round(mp, 2),
    )
