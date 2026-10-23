"""Geometric diagnostics computed from a real COLMAP sparse model.

Everything here is measured from the model. Reprojection error is in pixels at the *working*
resolution. Nothing is metric: monocular SfM has no scale unless a ground-truth source supplies it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pycolmap
from pydantic import BaseModel

from .prep import PreparedImage


class QualityVerdict(BaseModel):
    level: str  # good | fair | poor
    reasons: list[str]
    rubric: str = (
        "Heuristic rubric, not accuracy: good = >=90% images registered, mean reprojection <=1.0 px, "
        "mean track length >=3.5, median triangulation angle >=3 deg. poor = <60% registered, "
        "mean reprojection >2.0 px, or median triangulation angle <1.5 deg. Otherwise fair. "
        "Low reprojection error does not prove geometric accuracy."
    )


class SfMMetrics(BaseModel):
    n_input_images: int
    n_registered: int
    registered_fraction: float
    unregistered: list[str]
    n_points3d: int
    n_observations: int
    mean_observations_per_image: float
    track_length_mean: float
    track_length_median: float
    track_length_hist: dict[str, int]
    reproj_error_px_mean: float
    reproj_error_px_median: float
    reproj_error_px_p90: float
    reproj_error_px_max: float
    triangulation_angle_deg_median: float
    triangulation_angle_deg_p10: float
    extent_model_units: list[float]
    per_image_error: list[dict[str, Any]]  # name, n_obs, mean reprojection error (px)
    cameras: list[dict[str, Any]]
    quality: QualityVerdict
    units_note: str = (
        "Model units are arbitrary (monocular). Reprojection error is in pixels at working "
        "resolution. Point count is not an accuracy measure."
    )


def _tri_angles_deg(rec: pycolmap.Reconstruction) -> np.ndarray:
    centers = {
        iid: np.asarray(im.projection_center(), dtype=float)
        for iid, im in rec.images.items()
        if im.has_pose
    }
    out: list[float] = []
    for p in rec.points3D.values():
        cs = [centers[e.image_id] for e in p.track.elements if e.image_id in centers]
        if len(cs) < 2:
            continue
        rays = np.asarray(cs) - np.asarray(p.xyz)
        rays /= np.linalg.norm(rays, axis=1, keepdims=True) + 1e-12
        cos_min = float((rays @ rays.T).min())
        out.append(float(np.degrees(np.arccos(np.clip(cos_min, -1, 1)))))
    return np.asarray(out) if out else np.zeros(1)


def judge(frac: float, mean_err: float, mean_track: float, med_angle: float) -> QualityVerdict:
    reasons: list[str] = []
    poor = False
    if frac < 0.6:
        poor = True
        reasons.append(f"only {frac:.0%} of images registered")
    if mean_err > 2.0:
        poor = True
        reasons.append(f"mean reprojection error {mean_err:.2f} px > 2.0")
    if med_angle < 1.5:
        poor = True
        reasons.append(f"median triangulation angle {med_angle:.2f} deg < 1.5 (weak baseline)")
    if poor:
        return QualityVerdict(level="poor", reasons=reasons)
    good = frac >= 0.9 and mean_err <= 1.0 and mean_track >= 3.5 and med_angle >= 3.0
    if good:
        return QualityVerdict(level="good", reasons=["all rubric thresholds met"])
    notes = []
    if frac < 0.9:
        notes.append(f"{frac:.0%} registered")
    if mean_err > 1.0:
        notes.append(f"mean reprojection {mean_err:.2f} px")
    if mean_track < 3.5:
        notes.append(f"mean track length {mean_track:.2f}")
    if med_angle < 3.0:
        notes.append(f"median triangulation angle {med_angle:.2f} deg")
    return QualityVerdict(level="fair", reasons=notes)


def compute_metrics(rec: pycolmap.Reconstruction, prepared: list[PreparedImage]) -> SfMMetrics:
    registered_names = {im.name for im in rec.images.values() if im.has_pose}
    unreg = [p.original_name for p in prepared if p.safe_name not in registered_names]
    n_reg = len(registered_names)
    pts = list(rec.points3D.values())
    errs = np.array([p.error for p in pts], dtype=float) if pts else np.zeros(1)
    tracks = np.array([p.track.length() for p in pts], dtype=int) if pts else np.zeros(1, int)
    hist = {
        "2": int((tracks == 2).sum()),
        "3": int((tracks == 3).sum()),
        "4": int((tracks == 4).sum()),
        "5-9": int(((tracks >= 5) & (tracks <= 9)).sum()),
        "10+": int((tracks >= 10).sum()),
    }
    ang = _tri_angles_deg(rec)
    xyz = np.array([p.xyz for p in pts]) if pts else np.zeros((1, 3))
    lo, hi = np.percentile(xyz, [1, 99], axis=0)
    scale_by_name = {p.safe_name: p.scale for p in prepared}
    original = {p.safe_name: p.original_name for p in prepared}
    per_image: list[dict[str, Any]] = []
    for im in rec.images.values():
        if not im.has_pose:
            continue
        e = [
            rec.points3D[p.point3D_id].error
            for p in im.points2D
            if p.has_point3D() and p.point3D_id in rec.points3D
        ]
        per_image.append(
            {
                "name": original.get(im.name, im.name),
                "n_obs": len(e),
                "mean_error_px": round(float(np.mean(e)), 4) if e else None,
            }
        )
    cams = []
    for cid, c in sorted(rec.cameras.items()):
        # Express focal length at original resolution using the mean prepare scale.
        s = float(np.mean(list(scale_by_name.values()))) if scale_by_name else 1.0
        focal = float(c.mean_focal_length())
        cams.append(
            {
                "camera_id": int(cid),
                "model": c.model_name,
                "width": int(c.width),
                "height": int(c.height),
                "params": [float(x) for x in c.params],
                "focal_px_working": round(focal, 3),
                "focal_px_original_resolution": round(focal / s, 3),
                "prior_focal_from_exif": bool(c.has_prior_focal_length),
            }
        )
    frac = n_reg / len(prepared) if prepared else 0.0
    mean_err = float(rec.compute_mean_reprojection_error()) if pts else 0.0
    mean_track = float(rec.compute_mean_track_length()) if pts else 0.0
    med_ang = float(np.median(ang))
    return SfMMetrics(
        n_input_images=len(prepared),
        n_registered=n_reg,
        registered_fraction=round(frac, 4),
        unregistered=unreg,
        n_points3d=int(rec.num_points3D()),
        n_observations=int(rec.compute_num_observations()),
        mean_observations_per_image=round(float(rec.compute_mean_observations_per_reg_image()), 2),
        track_length_mean=round(mean_track, 3),
        track_length_median=float(np.median(tracks)),
        track_length_hist=hist,
        reproj_error_px_mean=round(mean_err, 4),
        reproj_error_px_median=round(float(np.median(errs)), 4),
        reproj_error_px_p90=round(float(np.percentile(errs, 90)), 4),
        reproj_error_px_max=round(float(errs.max()), 4),
        triangulation_angle_deg_median=round(med_ang, 3),
        triangulation_angle_deg_p10=round(float(np.percentile(ang, 10)), 3),
        extent_model_units=[round(float(x), 4) for x in (hi - lo)],
        per_image_error=per_image,
        cameras=cams,
        quality=judge(frac, mean_err, mean_track, med_ang),
    )
