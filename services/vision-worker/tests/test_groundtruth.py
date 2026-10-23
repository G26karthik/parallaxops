from __future__ import annotations

import numpy as np

from parallaxops_vision.groundtruth import GTImage, evaluate_poses, quat_to_rot, umeyama


def _random_rot(rng: np.random.Generator) -> np.ndarray:
    q = rng.normal(size=4)
    return quat_to_rot(*q)


def test_umeyama_recovers_known_similarity() -> None:
    rng = np.random.default_rng(0)
    src = rng.normal(size=(30, 3))
    r, s, t = _random_rot(rng), 2.5, np.array([1.0, -2.0, 0.5])
    dst = s * (r @ src.T).T + t
    s_est, r_est, t_est = umeyama(src, dst)
    assert abs(s_est - s) < 1e-9
    assert np.allclose(r_est, r, atol=1e-9)
    assert np.allclose(t_est, t, atol=1e-9)


def test_evaluate_poses_tolerates_one_gross_outlier() -> None:
    rng = np.random.default_rng(1)
    gt, est = {}, {}
    r_a, s, t_a = _random_rot(rng), 0.5, np.array([3.0, 1.0, -1.0])
    for i in range(12):
        r_cw = _random_rot(rng)
        c_gt = rng.normal(size=3) * 3
        gt[f"{i}.jpg"] = GTImage(f"{i}.jpg", r_cw, -r_cw @ c_gt)
        # Estimated frame is the GT frame mapped by the inverse similarity.
        c_est = r_a.T @ (c_gt - t_a) / s
        r_est = r_cw @ r_a
        if i == 0:
            c_est = c_est + np.array([40.0, 0, 0])  # gross registration failure
        est[f"{i}.jpg"] = (r_est, -r_est @ c_est)
    ev = evaluate_poses(est, gt)
    assert ev.n_inliers == 11
    assert ev.ate_rmse_inliers < 1e-6
    assert ev.ate_rmse_all > ev.ate_rmse_inliers  # honesty: 'all' includes the outlier
    assert abs(ev.scale_gt_units_per_model_unit - s) < 1e-4
