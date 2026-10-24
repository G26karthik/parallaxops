"""2D masks of people (or other COCO "thing" classes) for SfM feature extraction.

Runs in its own environment because it needs PyTorch (see docs/EXPERIMENT_MASKING.md):

    <venv-mask>/python benchmarks/masking/segment_masks.py --images DIR --out DIR

The model is DETR (ResNet-50, COCO panoptic), Apache-2.0 on its model card. It segments pixels in
each photo. That is 2D panoptic segmentation, used only to keep SfM from extracting features on
moving objects; nothing here is 3D segmentation. Output per image: <stem>.png, 255 = keep the
pixel, 0 = ignore it (COLMAP mask convention), plus masks_log.json with timings and coverage.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from scipy.ndimage import binary_dilation
from transformers import AutoImageProcessor, DetrForSegmentation

MODEL = "facebook/detr-resnet-50-panoptic"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--classes", default="person", help="comma separated COCO class names")
    ap.add_argument("--threshold", type=float, default=0.85, help="segment confidence")
    ap.add_argument("--dilate", type=int, default=6, help="grow masks by this many pixels")
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    args.out.mkdir(parents=True, exist_ok=True)
    processor = AutoImageProcessor.from_pretrained(MODEL)
    model = DetrForSegmentation.from_pretrained(MODEL).eval()
    wanted_names = {c.strip() for c in args.classes.split(",") if c.strip()}
    wanted = {i for i, label in model.config.id2label.items() if label in wanted_names}
    if len(wanted) != len(wanted_names):
        raise SystemExit(f"classes {wanted_names} not all known to the model")

    files = sorted(p for p in args.images.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    per_image: dict[str, dict[str, float]] = {}
    t_all = time.perf_counter()
    for p in files:
        t0 = time.perf_counter()
        with Image.open(p) as im:
            rgb = im.convert("RGB")
        with torch.no_grad():
            out = model(**processor(images=rgb, return_tensors="pt"))
        res = processor.post_process_panoptic_segmentation(
            out, threshold=args.threshold, target_sizes=[rgb.size[::-1]]
        )[0]
        seg = res["segmentation"].cpu().numpy()
        hit = np.zeros(seg.shape, dtype=bool)
        for s in res["segments_info"]:
            if s["label_id"] in wanted:
                hit |= seg == s["id"]
        if args.dilate and hit.any():
            hit = binary_dilation(hit, iterations=args.dilate)
        Image.fromarray(np.where(hit, 0, 255).astype("u1")).save(args.out / f"{p.stem}.png")
        per_image[p.name] = {
            "ignored_fraction": round(float(hit.mean()), 5),
            "seconds": round(time.perf_counter() - t0, 3),
        }
    log = {
        "model": MODEL,
        "model_commit": getattr(model.config, "_commit_hash", None),
        "torch": torch.__version__,
        "device": "cpu",
        "classes": sorted(wanted_names),
        "threshold": args.threshold,
        "dilate_px": args.dilate,
        "n_images": len(files),
        "total_seconds": round(time.perf_counter() - t_all, 2),
        "per_image": per_image,
    }
    (args.out / "masks_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    fr = [v["ignored_fraction"] for v in per_image.values()]
    print(
        f"{len(files)} images, {log['total_seconds']} s, ignored pixels mean "
        f"{np.mean(fr):.3f} max {np.max(fr):.3f}"
    )


if __name__ == "__main__":
    main()
