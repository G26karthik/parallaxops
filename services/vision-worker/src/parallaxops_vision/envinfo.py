"""Host and tool version capture for manifests and benchmarks. No personal paths or identifiers."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from typing import Any

import psutil


def _gpu() -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        name, mem, drv = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"name": name, "vram_mib": int(mem), "driver": drv}
    except Exception:
        return None


def tool_versions() -> dict[str, str]:
    import cv2
    import numpy
    import open3d
    import pycolmap

    return {
        "python": sys.version.split()[0],
        "pycolmap": pycolmap.__version__,
        "pycolmap_has_cuda": str(pycolmap.has_cuda),
        "open3d": open3d.__version__,
        "opencv": cv2.__version__,
        "numpy": numpy.__version__,
    }


def host_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "cpu_logical_cores": os.cpu_count(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "ram_total_gib": round(vm.total / 1024**3, 1),
        "gpu": _gpu(),
    }
