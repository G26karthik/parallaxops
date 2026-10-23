# Datasets

No dataset is redistributed by this repository. `scripts/fetch_dataset.py` downloads data from the
original host into `$PARALLAXOPS_HOME/data`, which is git-ignored.

## ETH3D high-resolution multi-view (DSLR), scene `terrace`

| Field | Value |
|-------|-------|
| Origin | <https://www.eth3d.net/> (ETH Zurich) |
| Archive | `https://www.eth3d.net/data/terrace_dslr_jpg.7z` (223 MiB) |
| Content | 23 JPEG images, 6048x4032, Nikon D3X, EXIF focal length present, plus COLMAP-format calibration (`dslr_calibration_jpg/{cameras,images,points3D}.txt`) |
| Why this scene | Real photographs with **ground-truth camera poses** in a laser-scanner frame (metric). This lets us report true camera-pose error, not only reprojection proxies |
| Citation | Schoeps et al., *A Multi-View Stereo Benchmark with High-Resolution Images and Multi-Camera Videos*, CVPR 2017 |

### License status (read this before redistributing anything)

- The official pages reachable from the development machine did not state the data license in the
  text we could retrieve. A web search summary attributed to the ETH3D site the terms
  **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)**. **We could not
  independently confirm this against the original page**, so we treat it as the operative license.
- Consequences: images and ground truth are **not committed or mirrored** here; use is limited to
  non-commercial research/portfolio evaluation; derived artifacts that embed the imagery
  (point-cloud colours, rendered PNGs) are shared only as small research-demonstration outputs with
  attribution. If you reuse this repository commercially, replace the dataset with your own capture.
- The dataset-pipeline *software* from the ETH3D authors is BSD-licensed; that is separate from the data.

### Preparation applied by ParallaxOps

Images are decoded, EXIF-orientation-normalised and resized so the longest side is at most
1600 px (configurable) before COLMAP. The scale factor per image is recorded in `manifest.json`.
The ground-truth `images.txt` is used **only** by the evaluator (`vision reconstruct --ground-truth`)
and is never given to the reconstruction.

## Own captures

Any folder of 15-60 overlapping JPG/PNG photos works. See the capture guide in the README.
