"""SfM engine. Runs in a CHILD PROCESS so the supervisor can kill it (and any children) on cancel,
timeout or budget breach. A pycolmap call cannot be interrupted safely in-process.

    python -m parallaxops_vision.engine <request.json>

Writes <workspace>/engine_result.json and appends progress events to <workspace>/progress.jsonl.
Handled failures are reported as a typed diagnostic inside engine_result.json (exit code 0);
anything else exits non-zero and the supervisor classifies it as ENGINE_CRASH.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import pycolmap

from .config import ReconstructionConfig
from .diagnostics import Diagnostic, DiagnosticCode, ReconstructionError

_MAX_PAIR_ID = 2_147_483_647


def pair_to_ids(pair_id: int) -> tuple[int, int]:
    return pair_id // _MAX_PAIR_ID, pair_id % _MAX_PAIR_ID


def choose_matching(n_images: int, cfg: ReconstructionConfig) -> tuple[str, str]:
    """Return (policy, rationale). Explicit user policy wins; otherwise by image count."""
    if cfg.matching != "auto":
        return cfg.matching, f"explicitly configured: {cfg.matching}"
    if n_images <= 60:
        n_pairs = n_images * (n_images - 1) // 2
        return "exhaustive", f"{n_images} images <= 60: exhaustive ({n_pairs} pairs) is affordable"
    return (
        "sequential",
        f"{n_images} images > 60: sequential matching (overlap={cfg.sequential_overlap}); "
        "assumes filenames follow capture order",
    )


def opts_to_dict(obj: Any, depth: int = 0) -> Any:
    """Serialize a pycolmap options object for the manifest (invocation arguments are persisted)."""
    if depth > 4:
        return repr(obj)
    if isinstance(obj, (bool, int, float, str, type(None))):
        return obj
    if isinstance(obj, os.PathLike):  # recursing into Path objects explodes the dump
        return Path(obj).as_posix()
    if isinstance(obj, (list, tuple)):
        return [opts_to_dict(x, depth + 1) for x in obj]
    if hasattr(obj, "name") and hasattr(obj, "value") and not hasattr(obj, "__dict__"):
        return str(obj)
    out: dict[str, Any] = {}
    for a in dir(obj):
        if a.startswith("_"):
            continue
        try:
            v = getattr(obj, a)
        except Exception:
            continue
        if callable(v):
            continue
        out[a] = opts_to_dict(v, depth + 1)
    return out if out else str(obj)


class Progress:
    def __init__(self, workspace: Path):
        self.path = workspace / "progress.jsonl"
        self.t0 = time.perf_counter()

    def emit(self, stage: str, state: str, **extra: Any) -> None:
        rec = {
            "stage": stage,
            "state": state,
            "t": round(time.perf_counter() - self.t0, 3),
            **extra,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def graph_stats(db_path: Path, n_images: int, min_inliers: int) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        kp = [r[0] for r in con.execute("SELECT rows FROM keypoints")]
        pairs = [
            (r[0], r[1])
            for r in con.execute(
                "SELECT pair_id, rows FROM two_view_geometries WHERE rows >= ?", (min_inliers,)
            )
        ]
    finally:
        con.close()
    parent = list(range(n_images + 2))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pid, _ in pairs:
        a, b = pair_to_ids(pid)
        parent[find(a)] = find(b)
    comps: dict[int, int] = {}
    for i in range(1, n_images + 1):
        comps[find(i)] = comps.get(find(i), 0) + 1
    kp_sorted = sorted(kp)
    return {
        "images_with_keypoints": len(kp),
        "median_keypoints": kp_sorted[len(kp_sorted) // 2] if kp_sorted else 0,
        "verified_pairs": len(pairs),
        "inlier_counts_median": sorted(r for _, r in pairs)[len(pairs) // 2] if pairs else 0,
        "connected_components": sorted(comps.values(), reverse=True),
    }


def run(request: dict[str, Any]) -> dict[str, Any]:
    ws = Path(request["workspace"])
    images = Path(request["images"])
    cfg = ReconstructionConfig.model_validate(request["config"])
    n_images = len(list(images.glob("*.jpg")))
    prog = Progress(ws)
    db_path = ws / "database.db"
    sparse = ws / "sparse"
    sparse.mkdir(parents=True, exist_ok=True)
    stages: dict[str, float] = {}
    invocation: dict[str, Any] = {}

    pycolmap.set_random_seed(cfg.seed)

    def timed(name: str, fn: Any) -> Any:
        prog.emit(name, "start")
        t = time.perf_counter()
        res = fn()
        stages[name] = round(time.perf_counter() - t, 3)
        prog.emit(name, "end", seconds=stages[name])
        return res

    # --- features ---
    fx = pycolmap.FeatureExtractionOptions()
    fx.max_image_size = cfg.max_image_size
    fx.num_threads = cfg.num_threads
    fx.use_gpu = False
    fx.sift.max_num_features = cfg.max_num_features
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = cfg.camera_model
    if request.get("masks"):
        reader.mask_path = request["masks"]  # features are not extracted where a mask is 0
    mode = pycolmap.CameraMode.SINGLE if cfg.single_camera else pycolmap.CameraMode.AUTO
    invocation["extract_features"] = {
        "extraction_options": opts_to_dict(fx),
        "reader_options": opts_to_dict(reader),
        "camera_mode": str(mode),
        "device": "cpu",
    }
    timed(
        "feature_extraction",
        lambda: pycolmap.extract_features(
            db_path,
            images,
            camera_mode=mode,
            reader_options=reader,
            extraction_options=fx,
            device=pycolmap.Device.cpu,
        ),
    )
    g = graph_stats(db_path, n_images, 0)
    if g["images_with_keypoints"] < max(2, n_images // 2) or g["median_keypoints"] < 100:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.NO_FEATURES,
                f"Median {g['median_keypoints']} keypoints/image; too few to match.",
                stage="feature_extraction",
                **g,
            )
        )

    # --- matching ---
    policy, why = choose_matching(n_images, cfg)
    fm = pycolmap.FeatureMatchingOptions()
    fm.use_gpu = False
    fm.num_threads = cfg.num_threads
    if policy == "exhaustive":
        match_fn = lambda: pycolmap.match_exhaustive(  # noqa: E731
            db_path, matching_options=fm, device=pycolmap.Device.cpu
        )
    elif policy == "sequential":
        po = pycolmap.SequentialPairingOptions()
        po.overlap = cfg.sequential_overlap
        match_fn = lambda: pycolmap.match_sequential(  # noqa: E731
            db_path, matching_options=fm, pairing_options=po, device=pycolmap.Device.cpu
        )
    else:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.INTERNAL,
                f"matching policy {policy!r} needs GPS priors, which this input lacks",
                stage="matching",
            )
        )
    invocation["matching"] = {
        "policy": policy,
        "rationale": why,
        "matching_options": opts_to_dict(fm),
    }
    timed("feature_matching", match_fn)
    g = graph_stats(db_path, n_images, cfg.min_num_matches)
    if g["verified_pairs"] == 0:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.INSUFFICIENT_MATCHES,
                "No image pair passed geometric verification.",
                stage="feature_matching",
                **g,
            )
        )

    # --- mapping ---
    mapped: dict[int, pycolmap.Reconstruction]
    if cfg.mapper == "incremental":
        mo = pycolmap.IncrementalPipelineOptions()
        mo.num_threads = cfg.num_threads
        mo.min_num_matches = cfg.min_num_matches
        mo.min_model_size = cfg.min_model_size
        mo.random_seed = cfg.seed
        mo.mapper.random_seed = cfg.seed
        mo.triangulation.random_seed = cfg.seed
        mo.max_runtime_seconds = int(request.get("remaining_seconds") or -1)
        mo.extract_colors = True
        invocation["mapping"] = {"kind": "incremental", "options": opts_to_dict(mo)}
        mapped = timed(
            "sparse_mapping", lambda: pycolmap.incremental_mapping(db_path, images, sparse, mo)
        )
    else:
        go = pycolmap.GlobalPipelineOptions()
        invocation["mapping"] = {"kind": "global", "options": opts_to_dict(go)}
        mapped = timed(
            "sparse_mapping", lambda: pycolmap.global_mapping(db_path, images, sparse, go)
        )

    if not mapped:
        code = (
            DiagnosticCode.NO_INITIAL_PAIR
            if g["verified_pairs"] < 3
            else DiagnosticCode.SFM_NO_MODEL
        )
        raise ReconstructionError(
            Diagnostic.of(code, "Mapper returned no reconstruction.", stage="sparse_mapping", **g)
        )

    best_id = max(mapped, key=lambda k: (mapped[k].num_reg_images(), mapped[k].num_points3D()))
    models = [
        {
            "model_id": int(k),
            "registered_images": int(r.num_reg_images()),
            "points3d": int(r.num_points3D()),
        }
        for k, r in sorted(mapped.items())
    ]
    return {
        "ok": True,
        "stages_seconds": stages,
        "matching_graph": g,
        "invocation": invocation,
        "models": models,
        "best_model_id": int(best_id),
        "best_model_dir": f"sparse/{best_id}",  # relative: published logs carry no host paths
        "n_input_images": n_images,
    }


def main(argv: list[str]) -> int:
    request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    ws = Path(request["workspace"])
    try:
        result = run(request)
    except ReconstructionError as exc:
        result = {"ok": False, "diagnostic": exc.diagnostic.model_dump(mode="json")}
    except Exception:
        (ws / "engine_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 3
    (ws / "engine_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
