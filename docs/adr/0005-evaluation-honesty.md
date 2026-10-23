# ADR 0005: Evaluation honesty rules

Status: accepted (Gate 1)

1. **Ground truth beats proxies.** Where ground-truth camera poses exist (ETH3D), accuracy is
   reported as camera-position error after a RANSAC + Umeyama similarity fit, plus rotation error.
   The fit's scale is only meaningful because the ground truth supplies one; monocular SfM alone
   never reports metric distances.
2. **Always report "all" and "inliers".** The inlier-only RMSE is optimistic by construction; the
   all-cameras RMSE is the headline.
3. **Proxies are labelled as proxies.** Reprojection error is a self-consistency measure.
   Point count is not accuracy. The `quality` verdict is a documented heuristic rubric.
4. **Variance first.** pycolmap uses multithreaded matching and bundle adjustment, so runs are not
   bit-reproducible even with a fixed seed (two back-to-back runs of the same scene gave 5449 and
   5432 points). Benchmarks run each configuration repeatedly and a difference inside the
   run-to-run spread is reported as inconclusive.
5. **Nothing is fabricated.** Every figure in the README comes from a committed result file in
   `benchmarks/results/` produced by a command documented next to it.
