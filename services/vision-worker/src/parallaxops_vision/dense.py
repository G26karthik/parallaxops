"""Optional dense reconstruction (multi-view stereo) with the COLMAP CUDA build.

Separate from the sparse path on purpose: it needs an NVIDIA GPU and a CUDA build of COLMAP (the
PyPI wheel is CPU-only), it is slow, and the platform never depends on it. If it is unavailable
the caller keeps the sparse result and records DENSE_UNAVAILABLE.

COLMAP is invoked as `colmap.exe` with an argv list. The `COLMAP.bat` launcher is never used:
Python would route it through cmd.exe, which re-parses arguments.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic, DiagnosticCode, ReconstructionError
from .supervisor import run_supervised

MIN_FREE_VRAM_MIB = 2048


def find_colmap_cuda() -> Path | None:
    """The CUDA COLMAP binary: $COLMAP_CUDA_EXE, else <home>/tools/colmap-cuda/bin/colmap(.exe)."""
    env = os.environ.get("COLMAP_CUDA_EXE")
    if env and Path(env).is_file():
        return Path(env)
    home = os.environ.get("PARALLAXOPS_HOME")
    if not home:
        base = os.environ.get("LOCALAPPDATA")
        home = (
            str(Path(base) / "ParallaxOps")
            if base
            else str(Path.home() / ".local/share/parallaxops")
        )
    for name in ("colmap.exe", "colmap"):
        p = Path(home) / "tools" / "colmap-cuda" / "bin" / name
        if p.is_file():
            return p
    return None


def gpu_memory_mib() -> tuple[int, int] | None:
    """(used, total) MiB of GPU 0 from nvidia-smi, or None if there is no NVIDIA GPU."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = (
            subprocess.run(
                [exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            .stdout.strip()
            .splitlines()[0]
        )
        used, total = (int(x) for x in out.split(","))
        return used, total
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def check_capability(colmap: Path | None) -> None:
    """Raise a typed diagnostic when dense reconstruction cannot run on this host."""
    if colmap is None:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.DENSE_UNAVAILABLE,
                "No CUDA build of COLMAP is installed, so dense reconstruction was skipped.",
                stage="dense",
            )
        )
    mem = gpu_memory_mib()
    if mem is None:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.DENSE_UNAVAILABLE, "No NVIDIA GPU is visible.", stage="dense"
            )
        )
    free = mem[1] - mem[0]
    if free < MIN_FREE_VRAM_MIB:
        raise ReconstructionError(
            Diagnostic.of(
                DiagnosticCode.DENSE_UNAVAILABLE,
                f"Only {free} MiB of GPU memory is free; dense reconstruction needs {MIN_FREE_VRAM_MIB} MiB.",
                stage="dense",
                free_mib=free,
            )
        )


class _VramSampler:
    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            m = gpu_memory_mib()
            if m:
                self.peak = max(self.peak, m[0])
            self._stop.wait(1.0)

    def __enter__(self) -> _VramSampler:
        self._t.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._t.join(timeout=3)


def run_dense(
    sparse_model: Path,
    images: Path,
    work: Path,
    out_ply: Path,
    *,
    max_image_size: int = 1000,
    geom_consistency: bool = True,
    timeout_s: float = 1800.0,
    max_rss_bytes: int = 12 * 1024**3,
    cancel: threading.Event | None = None,
    colmap: Path | None = None,
    on_stage: Callable[[str, str, float | None], None] | None = None,
) -> dict[str, Any]:
    """undistort -> PatchMatch stereo -> fusion. Returns a measured report; raises on failure."""
    exe = colmap or find_colmap_cuda()
    check_capability(exe)
    assert exe is not None
    work.mkdir(parents=True, exist_ok=True)
    dense = work / "dense"
    env = {"PATH": f"{exe.parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    t_start = time.perf_counter()
    stages: dict[str, float] = {}

    def step(name: str, *args: str) -> None:
        if on_stage:
            on_stage(name, "start", None)
        t = time.perf_counter()
        remaining = timeout_s - (time.perf_counter() - t_start)
        res = run_supervised(
            [str(exe), *args],
            cwd=work,
            log_path=work / f"{name}.log",
            timeout_s=max(5.0, remaining),
            max_rss_bytes=max_rss_bytes,
            workspace=dense if dense.exists() else None,
            max_workspace_bytes=12 * 1024**3,
            cancel=cancel,
            env=env,
        )
        stages[name] = round(time.perf_counter() - t, 2)
        if on_stage:
            on_stage(name, "end", stages[name])
        if res.killed_reason:
            code = {
                "timeout": DiagnosticCode.TIMEOUT,
                "cancelled": DiagnosticCode.CANCELLED,
                "memory": DiagnosticCode.MEMORY_BUDGET_EXCEEDED,
                "disk": DiagnosticCode.DISK_BUDGET_EXCEEDED,
            }[res.killed_reason]
            raise ReconstructionError(
                Diagnostic.of(
                    code, f"Dense stage '{name}' stopped: {res.killed_reason}.", stage="dense"
                )
            )
        if res.returncode != 0:
            raise ReconstructionError(
                Diagnostic.of(
                    DiagnosticCode.DENSE_FAILED,
                    f"COLMAP {name} exited with code {res.returncode}.",
                    stage="dense",
                    log_tail=res.log_tail[-800:],
                )
            )

    with _VramSampler() as vram:
        step(
            "dense_undistort",
            "image_undistorter",
            "--image_path", str(images),
            "--input_path", str(sparse_model),
            "--output_path", str(dense),
            "--output_type", "COLMAP",
            "--max_image_size", str(max_image_size),
        )  # fmt: skip
        step(
            "dense_patch_match",
            "patch_match_stereo",
            "--workspace_path", str(dense),
            "--workspace_format", "COLMAP",
            "--PatchMatchStereo.max_image_size", str(max_image_size),
            "--PatchMatchStereo.geom_consistency", "true" if geom_consistency else "false",
            "--PatchMatchStereo.gpu_index", "0",
        )  # fmt: skip
        step(
            "dense_fusion",
            "stereo_fusion",
            "--workspace_path", str(dense),
            "--workspace_format", "COLMAP",
            "--input_type", "geometric" if geom_consistency else "photometric",
            "--output_path", str(out_ply),
        )  # fmt: skip

    import open3d as o3d

    n_points = len(o3d.io.read_point_cloud(out_ply).points)
    if n_points == 0:
        raise ReconstructionError(
            Diagnostic.of(DiagnosticCode.DENSE_FAILED, "Fusion produced no points.", stage="dense")
        )
    version = subprocess.run(
        [str(exe), "help"], capture_output=True, text=True, timeout=30, env={**os.environ, **env}
    ).stdout.splitlines()[:1]
    return {
        "n_points": n_points,
        "max_image_size": max_image_size,
        "geom_consistency": geom_consistency,
        "stages_seconds": stages,
        "total_seconds": round(time.perf_counter() - t_start, 2),
        "peak_vram_mib": vram.peak,
        "colmap": version[0] if version else "unknown",
        "note": "Dense MVS point cloud (geometrically consistent depth fusion). Not a mesh; scale arbitrary.",
    }
