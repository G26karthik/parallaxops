"""Ground-truth camera pose evaluation (e.g. ETH3D calibration in COLMAP text format).

Monocular SfM has no metric scale of its own. We fit a similarity transform (Umeyama, RANSAC
for robustness) from estimated camera centres to ground-truth centres and report residuals in the
GT unit. The recovered scale is only meaningful because the GT supplies one (laser-scan frame).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import BaseModel


@dataclass
class GTImage:
    name: str  # basename
    R_cw: np.ndarray  # world -> camera rotation (3x3)
    t_cw: np.ndarray  # world -> camera translation (3,)

    @property
    def center(self) -> np.ndarray:
        return -self.R_cw.T @ self.t_cw


def quat_to_rot(qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
    q = np.array([qw, qx, qy, qz], dtype=float)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def load_colmap_text_images(images_txt: Path) -> dict[str, GTImage]:
    """Parse COLMAP images.txt (two lines per image: pose line, then 2D points)."""
    lines = [
        ln for ln in images_txt.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")
    ]
    out: dict[str, GTImage] = {}
    for ln in lines[::2]:
        p = ln.split()
        if len(p) < 10:
            continue
        qw, qx, qy, qz, tx, ty, tz = map(float, p[1:8])
        name = Path(p[9]).name
        out[name] = GTImage(name, quat_to_rot(qw, qx, qy, qz), np.array([tx, ty, tz]))
    return out


def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Least-squares similarity dst ~ s * R @ src + t for Nx3 point sets."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    u, d, vt = np.linalg.svd(cov)
    sgn = np.ones(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sgn[-1] = -1
    rot = u @ np.diag(sgn) @ vt
    var_s = (xs**2).sum() / len(src)
    scale = float((d * sgn).sum() / var_s) if var_s > 0 else 1.0
    return scale, rot, mu_d - scale * rot @ mu_s


def ransac_umeyama(
    src: np.ndarray, dst: np.ndarray, thresh: float, iters: int = 500, seed: int = 0
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    n = len(src)
    rng = np.random.default_rng(seed)
    best_inl = np.zeros(n, bool)
    if n >= 4:
        for _ in range(iters):
            idx = rng.choice(n, 3, replace=False)
            try:
                s, r, t = umeyama(src[idx], dst[idx])
            except np.linalg.LinAlgError:
                continue
            inl = np.linalg.norm(dst - (s * (r @ src.T).T + t), axis=1) < thresh
            if inl.sum() > best_inl.sum():
                best_inl = inl
    if best_inl.sum() < 3:
        best_inl = np.ones(n, bool)
    s, r, t = umeyama(src[best_inl], dst[best_inl])
    return s, r, t, best_inl


class PoseEval(BaseModel):
    n_registered: int
    n_matched_to_gt: int
    n_inliers: int
    scale_gt_units_per_model_unit: float
    ate_rmse_all: float
    ate_rmse_inliers: float
    ate_median: float
    ate_max: float
    gt_camera_spread: float
    ate_rmse_all_over_spread: float
    rotation_error_deg_median: float
    rotation_error_deg_max: float
    per_image: dict[str, dict[str, float]]
    note: str = (
        "Errors are in the GT unit after a RANSAC+Umeyama similarity fit. 'all' includes images "
        "the fit rejected; 'inliers' is optimistic by construction. The scale comes from GT."
    )


def evaluate_poses(
    est: dict[str, tuple[np.ndarray, np.ndarray]], gt: dict[str, GTImage]
) -> PoseEval:
    """est maps GT-comparable image name -> (R_cw, t_cw) in the estimated model frame."""
    names = sorted(n for n in est if n in gt)
    if len(names) < 3:
        raise ValueError(f"need >= 3 images shared with ground truth, found {len(names)}")
    c_est = np.array([-est[n][0].T @ est[n][1] for n in names])
    c_gt = np.array([gt[n].center for n in names])
    spread = float(np.linalg.norm(c_gt.max(0) - c_gt.min(0)))
    s, r_a, t, inl = ransac_umeyama(c_est, c_gt, thresh=0.05 * spread)
    err = np.linalg.norm(c_gt - (s * (r_a @ c_est.T).T + t), axis=1)
    rot_err = []
    for n in names:
        r_est_in_gt = est[n][0] @ r_a.T
        r_err = gt[n].R_cw @ r_est_in_gt.T
        cos = np.clip((np.trace(r_err) - 1) / 2, -1, 1)
        rot_err.append(float(np.degrees(np.arccos(cos))))
    return PoseEval(
        n_registered=len(est),
        n_matched_to_gt=len(names),
        n_inliers=int(inl.sum()),
        scale_gt_units_per_model_unit=round(s, 6),
        ate_rmse_all=round(float(np.sqrt((err**2).mean())), 5),
        ate_rmse_inliers=round(float(np.sqrt((err[inl] ** 2).mean())), 5),
        ate_median=round(float(np.median(err)), 5),
        ate_max=round(float(err.max()), 5),
        gt_camera_spread=round(spread, 4),
        ate_rmse_all_over_spread=round(float(np.sqrt((err**2).mean())) / spread, 5),
        rotation_error_deg_median=round(float(np.median(rot_err)), 3),
        rotation_error_deg_max=round(float(np.max(rot_err)), 3),
        per_image={
            n: {"position_error": round(float(e), 5), "rotation_error_deg": round(re, 3)}
            for n, e, re in zip(names, err, rot_err, strict=True)
        },
    )
