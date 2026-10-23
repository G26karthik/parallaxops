# Experiment plan: baseline vs quality-aware controller

Written and committed **before** any controller experiment was run. Results are in
`docs/EXPERIMENTS.md` and `benchmarks/results/gate4/`. If the controller loses or the evidence is
inconclusive, that is what gets reported.

## Policies under test

**Baseline.** One fixed, documented pipeline: working size 1600 px, 8192 SIFT features, exhaustive
matching, `SIMPLE_RADIAL` single camera, incremental mapper, seed 0. One attempt.

**Controller** (`parallaxops_vision/controller.py`). Same engine, same code, bounded recovery:

1. Input QC. If at most 30% of images are flagged blurry and at least 8 images would remain, exclude
   the blurry ones before attempt 1. Rationale is recorded.
2. Run the baseline configuration (attempt 1).
3. Accept if registered fraction >= 0.90 and mean reprojection error <= 1.5 px.
4. Otherwise pick the next unused strategy from a small fixed menu, chosen by the diagnosis:
   - low registration or fragmented model: more features (16384) and larger working size (x1.25);
     then the global mapper (GLOMAP);
   - high reprojection error: `OPENCV` camera model (models radial and tangential distortion);
     then exclude the images with the largest per-image residuals;
   - weak baseline (median triangulation angle < 3 deg): no strategy can fix this; the controller
     emits an explicit "capture more views with sideways movement" request.
5. Hard limits: at most 3 attempts; total wall clock no more than 3x the baseline budget; a
   strategy is skipped if its projected cost would exceed what remains.
6. The best attempt wins (higher registered fraction, then lower reprojection error). Every
   decision, its reason, its config and its outcome are written to `controller_log.json`.

Nothing in the controller sees ground truth.

## Data

Scene `terrace` (ETH3D, 23 DSLR images, 6048x4032, GT camera poses) is the primary scene. Scene
`relief` (31 images) is a second, smaller-sample scene run at 1200 px. Variants are generated
deterministically by `benchmarks/variants.py` from the originals; every transformation and seed is
recorded in `variants.json`. Variants:

| id | transformation (exact parameters in variants.json) |
|----|------------------------------------------------------|
| clean | no change (control) |
| blur | Gaussian blur, sigma 10 px at 1600 px width, on 40% of images |
| overlap | keep every 3rd image (low overlap, fewer views) |
| repeats | add 8 exact duplicates and 4 near-duplicates (noise sigma 2) |
| light | strong gamma (0.4) and gain/offset on 40% of images |
| moving | a large textured occluder (about 20% of the frame) at a different position per image on 50% of images |

## Metrics

Per run: registered fraction, sparse point count, mean/median reprojection error, mean track
length, median triangulation angle, wall clock (all attempts), CPU seconds, peak RSS, attempts.
**Ground-truth accuracy:** camera-position RMSE divided by the GT camera spread, and median rotation
error, after a RANSAC similarity fit (ADR 0005). Reprojection error and point count are proxies and
are never used alone to claim improvement.

## Replication and noise floor

Terrace: 3 repetitions per (variant, arm), seeds 0, 1, 2 (the seed is also the only controlled
source of variation; multithreading adds more). Relief: 1 repetition. The **noise floor** for a
metric is the min-max range across the 3 baseline repetitions of the same variant.

## Hypotheses and decision rule

- **H1 (helps where it matters).** On variants where the baseline's mean registered fraction is
  below 0.90, the controller's mean registered fraction is at least 10 percentage points higher and
  outside the baseline's min-max range, at no more than 2.5x the baseline wall clock.
- **H2 (no waste on easy data).** On `clean`, the controller accepts attempt 1 and adds no more
  than 5% wall clock over the baseline (QC overhead only).
- **H3 (no accuracy regression).** Where both arms register at least 90% of images, the
  controller's GT position error ratio is not worse than the baseline's noise-floor maximum.

A hypothesis is reported as **supported**, **not supported**, or **inconclusive** (difference inside
the noise floor, or too few repetitions). One scene with three repetitions cannot support a general
claim; the report says so.

## Out of scope

Metric accuracy in meters without a scale source; dense reconstruction; learned segmentation of
moving objects (Gate 5, experimental).
