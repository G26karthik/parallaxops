"""Scratch directory ownership and orphan sweeping.

A worker that is killed (crash, OOM, power loss) never runs its `finally` cleanup. Each attempt
therefore records the owning process id; a sweeper removes directories whose owner no longer
exists. Without an owner file a directory is only removed once it is clearly old.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import psutil

from .artifacts import remove_tree

OWNER_FILE = ".owner"
UNOWNED_MAX_AGE_S = 3 * 3600  # longer than any heavy activity may run


def claim(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / OWNER_FILE).write_text(str(os.getpid()), encoding="utf-8")


def _owner_alive(path: Path) -> bool | None:
    try:
        pid = int((path / OWNER_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return psutil.pid_exists(pid)


def sweep_orphans(root: Path, now: float | None = None) -> tuple[int, int]:
    """Remove scratch dirs of dead workers. Returns (directories removed, bytes freed)."""
    if not root.exists():
        return 0, 0
    now = now if now is not None else time.time()
    removed = freed = 0
    # attempt dirs live at root/<job>/attempt-N and root/preflight/<id>
    candidates = [p for job in root.iterdir() if job.is_dir() for p in job.iterdir() if p.is_dir()]
    for d in candidates:
        alive = _owner_alive(d)
        stale = alive is False or (alive is None and now - d.stat().st_mtime > UNOWNED_MAX_AGE_S)
        if stale:
            freed += remove_tree(d)
            removed += 1
    for job in list(root.iterdir()):
        if job.is_dir() and not any(job.iterdir()):
            job.rmdir()
    return removed, freed
