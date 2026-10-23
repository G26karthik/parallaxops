from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from parallaxops_vision.worker.scratch import UNOWNED_MAX_AGE_S, claim, sweep_orphans


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid  # exited, so no process owns this pid


def make(root: Path, job: str, attempt: str, owner: int | None, size: int = 1000) -> Path:
    d = root / job / attempt
    (d / "in").mkdir(parents=True)
    (d / "in" / "img.jpg").write_bytes(b"x" * size)
    if owner is not None:
        (d / ".owner").write_text(str(owner))
    return d


def test_dead_workers_scratch_is_swept_and_live_workers_is_kept(tmp_path: Path) -> None:
    dead = make(tmp_path, "job-dead", "attempt-1", dead_pid(), size=5000)
    live = make(tmp_path, "job-live", "attempt-1", os.getpid())
    removed, freed = sweep_orphans(tmp_path)
    assert (removed, freed) >= (1, 5000)
    assert not dead.exists() and not dead.parent.exists()  # empty job dir is removed too
    assert live.exists()


def test_unowned_dirs_are_only_removed_when_old(tmp_path: Path) -> None:
    d = make(tmp_path, "job-x", "attempt-1", None)
    assert sweep_orphans(tmp_path)[0] == 0  # fresh: could belong to an older worker version
    future = time.time() + UNOWNED_MAX_AGE_S + 60
    assert sweep_orphans(tmp_path, now=future)[0] == 1 and not d.exists()


def test_claim_records_the_current_pid(tmp_path: Path) -> None:
    claim(tmp_path / "a" / "b")
    assert (tmp_path / "a" / "b" / ".owner").read_text() == str(os.getpid())


def test_sweep_of_a_missing_root_is_a_noop(tmp_path: Path) -> None:
    assert sweep_orphans(tmp_path / "nope") == (0, 0)
