# Gate evidence

Each entry lists what was run, what passed, and where the raw output lives.

## Gate 0: environment audit (done)

- `docs/ENVIRONMENT.md` (host, versions, constraints); `docs/adr/0001-0005`.
- Findings that changed the design: Docker daemon off, 6.4 GiB free RAM, MinIO Windows binary gone
  (HTTP 410), pycolmap PyPI wheel has no CUDA, ETH3D landing page unreliable but data host works.

## Gate 1: vision core (done)

Commands:

```text
make test                 # ruff + mypy --strict + 19 unit tests (no downloads)
make test-integration     # real ETH3D terrace -> real COLMAP -> scored against GT poses
vision reconstruct --input <terrace images> --output runs/terrace --ground-truth <images.txt>
```

Results (all measured): `20 passed` (including the integration test), ruff and mypy clean.
Reconstruction: 23/23 images, 5,434 points, 0.845 px mean reprojection, GT camera RMSE 0.0240 over a
7.65 spread, 72.6 s on CPU. Raw files: `benchmarks/results/gate1-terrace/`. Images: `docs/assets/gate1/`.

Acceptance checks covered by tests:

| Brief requirement | Test |
|-------------------|------|
| nonempty COLMAP-derived PLY, poses, JSON metrics | `test_real_reconstruction_terrace` |
| typed diagnostic + nonzero exit on bad sets | `test_cli_exit_code_is_typed_on_bad_input`, `test_bad_set_publishes_typed_failure_and_leaves_no_staging` |
| unrelated images fail typed, not crash | `test_noise_images_fail_with_typed_diagnostic_not_crash` |
| process-tree kill, RSS/disk/time budgets | `tests/test_supervisor.py` |
| path escape / corrupt / oversize input | `tests/test_qc.py` |
| atomic artifact publication | `test_publish_atomic_refuses_clobber_without_force` |
| manifest checksums match files | `test_real_reconstruction_terrace` |

Known gaps carried forward: GPU profile, dense MVS, holdout-view reprojection metric, mixed-camera
datasets, matching beyond 60 images is untested on real data.
