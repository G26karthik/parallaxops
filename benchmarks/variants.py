"""Deterministic degraded variants of a real scene, with full provenance.

    python benchmarks/variants.py terrace          # writes $PARALLAXOPS_HOME/data/variants/terrace/<variant>/

Every transformation, parameter and seed is written to variants.json next to the images, together
with the SHA-256 of every source image. Source photos come from the ETH3D dataset (see
docs/DATASETS.md) and are never committed; only variants.json-style provenance is.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

WORKING_WIDTH = 1600  # variants are generated at the working size so degradations are what COLMAP sees


def home() -> Path:
    return Path(
        os.environ.get("PARALLAXOPS_HOME")
        or Path(os.environ["LOCALAPPDATA"]) / "ParallaxOps"
    )


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path) -> Image.Image:
    im = Image.open(p)
    im.load()
    exif = im.getexif()
    im.thumbnail((WORKING_WIDTH, WORKING_WIDTH), Image.Resampling.LANCZOS)
    im.info["exif"] = exif.tobytes()
    return im


def save(im: Image.Image, out: Path, exif: bytes | None = None) -> None:
    im.convert("RGB").save(
        out, "JPEG", quality=92, exif=exif or im.info.get("exif", b"")
    )


def pick(rng: np.random.Generator, n: int, frac: float) -> list[int]:
    k = max(1, round(frac * n))
    return sorted(rng.choice(n, size=k, replace=False).tolist())


def occluder(
    im: Image.Image, rng: np.random.Generator, frac_area: float
) -> Image.Image:
    """Paste a textured blob covering ~frac_area of the frame at a random position."""
    w, h = im.size
    bw, bh = int(w * np.sqrt(frac_area) * 1.1), int(h * np.sqrt(frac_area) * 1.1)
    x0, y0 = int(rng.integers(0, w - bw)), int(rng.integers(0, h - bh))
    tex = rng.integers(40, 220, (bh, bw, 3), dtype=np.uint8)
    blob = Image.fromarray(tex).filter(ImageFilter.GaussianBlur(3))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, bw - 1, bh - 1), fill=255)
    out = im.copy()
    out.paste(blob, (x0, y0), mask)
    return out


def make(scene_dir: Path, out_root: Path, seed: int = 1234) -> dict[str, Any]:
    files = sorted(scene_dir.glob("*.JPG"))
    n = len(files)
    rng = np.random.default_rng(seed)
    prov: dict[str, Any] = {
        "scene": scene_dir.parent.parent.name,
        "seed": seed,
        "working_width": WORKING_WIDTH,
        "sources": {f.name: sha(f) for f in files},
        "variants": {},
    }

    def emit(
        variant: str, images: dict[str, Image.Image], transform: dict[str, Any]
    ) -> None:
        d = out_root / variant
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.jpg"):
            old.unlink()
        for name, im in images.items():
            save(im, d / name)
        prov["variants"][variant] = {"n_images": len(images), "transform": transform}

    base = {f.name: load(f) for f in files}
    emit("clean", dict(base), {"type": "none"})

    idx = pick(rng, n, 0.4)
    blurred = {
        k: (v.filter(ImageFilter.GaussianBlur(10)) if i in idx else v)
        for i, (k, v) in enumerate(base.items())
    }
    emit(
        "blur",
        blurred,
        {
            "type": "gaussian_blur",
            "sigma_px": 10,
            "images": [files[i].name for i in idx],
        },
    )

    keep = list(range(0, n, 3))
    emit(
        "overlap",
        {files[i].name: base[files[i].name] for i in keep},
        {"type": "keep_every_3rd", "kept": [files[i].name for i in keep]},
    )

    dup_idx = rng.choice(n, size=8, replace=False).tolist()
    near_idx = rng.choice(n, size=4, replace=False).tolist()
    repeats = dict(base)
    for i in dup_idx:
        repeats[f"dup_{files[i].stem}.jpg"] = base[files[i].name]
    for i in near_idx:
        arr = np.asarray(base[files[i].name]).astype(np.int16)
        noisy = np.clip(arr + rng.normal(0, 2, arr.shape), 0, 255).astype(np.uint8)
        repeats[f"near_{files[i].stem}.jpg"] = Image.fromarray(noisy)
    emit(
        "repeats",
        repeats,
        {
            "type": "duplicates",
            "exact": [files[i].name for i in dup_idx],
            "near_noise_sigma": 2,
            "near": [files[i].name for i in near_idx],
        },
    )

    idx = pick(rng, n, 0.4)
    lit = {}
    for i, (k, v) in enumerate(base.items()):
        if i in idx:
            a = (np.asarray(v).astype(np.float32) / 255.0) ** 0.4  # strong gamma lift
            a = np.clip(a * 0.85 + 0.12, 0, 1)
            lit[k] = Image.fromarray((a * 255).astype(np.uint8))
        else:
            lit[k] = v
    emit(
        "light",
        lit,
        {
            "type": "gamma_gain",
            "gamma": 0.4,
            "gain": 0.85,
            "offset": 0.12,
            "images": [files[i].name for i in idx],
        },
    )

    idx = pick(rng, n, 0.5)
    moving = {
        k: (occluder(v, rng, 0.20) if i in idx else v)
        for i, (k, v) in enumerate(base.items())
    }
    emit(
        "moving",
        moving,
        {
            "type": "random_textured_occluder",
            "area_fraction": 0.20,
            "images": [files[i].name for i in idx],
        },
    )

    (out_root / "variants.json").write_text(
        json.dumps(prov, indent=2), encoding="utf-8"
    )
    return prov


if __name__ == "__main__":
    scene = sys.argv[1] if len(sys.argv) > 1 else "terrace"
    src = home() / "data" / "eth3d" / scene / scene / "images" / "dslr_images"
    out = home() / "data" / "variants" / scene
    p = make(src, out)
    for k, v in p["variants"].items():
        print(f"{k:8s} {v['n_images']} images")
