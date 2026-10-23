/** Plain-language explanation for every number the UI shows: what it is, where it came from, and
 * what it does NOT prove. Nothing here is a claim of accuracy. */
export const HELP = {
  registered: {
    label: "Images registered",
    source: "COLMAP incremental mapper",
    meaning:
      "Images the mapper could place in the model. A low fraction means weak overlap, blur, or untextured views. It says nothing about how accurate the placed cameras are.",
  },
  points: {
    label: "Sparse 3D points",
    source: "Triangulated SIFT feature tracks",
    meaning:
      "Points triangulated from matched features. This is a sparse skeleton, not a surface. More points does not mean more accurate geometry.",
  },
  reprojMean: {
    label: "Reprojection error (mean)",
    source: "Mean per-point error at working resolution",
    meaning:
      "How far, on average, each 3D point lands from where it was observed. A self-consistency check: low values do not prove geometric accuracy or correct scale.",
  },
  reprojMedian: {
    label: "Reprojection error (median)",
    source: "Median per-point error at working resolution",
    meaning: "Less sensitive to a few bad points than the mean.",
  },
  track: {
    label: "Mean track length",
    source: "Images that observe each 3D point",
    meaning:
      "Longer tracks mean points are confirmed by more views, which usually stabilises the solution.",
  },
  triAngle: {
    label: "Median triangulation angle",
    source: "Max ray angle per point, from camera centres",
    meaning:
      "Baseline strength. Below about 1.5 degrees depth is poorly constrained (cameras barely moved sideways).",
  },
  wall: {
    label: "Wall clock",
    source: "Worker, first attempt start to completion",
    meaning: "Measured on this machine, CPU only. Queue wait is reported separately.",
  },
  queue: {
    label: "Queue wait",
    source: "API, QUEUED to first attempt start",
    meaning: "Time spent waiting for the single heavy-job slot. Not part of reconstruction time.",
  },
  rss: {
    label: "Peak memory",
    source: "Sampled process-tree RSS every 0.5 s",
    meaning:
      "Resident memory of the reconstruction engine and its children; may slightly under-report spikes.",
  },
  denseCount: {
    label: "Dense 3D points",
    source: "COLMAP PatchMatch stereo + geometric fusion (GPU)",
    meaning:
      "Points from per-pixel depth estimation fused across views. Much denser than the sparse cloud, still a point cloud (no surface) with arbitrary scale. Density is not accuracy.",
  },
  denseTime: {
    label: "Dense time",
    source: "Worker, undistort + PatchMatch + fusion",
    meaning: "GPU stage wall clock, measured on this machine.",
  },
  vram: {
    label: "Peak GPU memory",
    source: "nvidia-smi sampled every second",
    meaning:
      "Device memory in use while the dense stage ran (includes other processes on the GPU).",
  },
} as const;

export const SCENE_NOTE =
  "Sparse SfM points (triangulated features), not a dense surface. Scale is arbitrary: monocular photos carry no metric scale.";
