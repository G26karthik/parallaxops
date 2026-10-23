from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import psutil

from parallaxops_vision.supervisor import run_supervised

SLEEPER = "import time; time.sleep(60)"


def _run(tmp_path: Path, code: str, **kw):  # type: ignore[no-untyped-def]
    defaults = {"timeout_s": 30, "max_rss_bytes": 4 * 1024**3, "tick_s": 0.1}
    return run_supervised(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        log_path=tmp_path / "log.txt",
        **{**defaults, **kw},
    )


def test_normal_exit_returns_code_and_captures_log(tmp_path: Path) -> None:
    r = _run(tmp_path, "print('hello'); raise SystemExit(5)")
    assert r.returncode == 5 and r.killed_reason is None
    assert "hello" in r.log_tail


def test_timeout_kills_process(tmp_path: Path) -> None:
    t0 = time.perf_counter()
    r = _run(tmp_path, SLEEPER, timeout_s=1)
    assert r.killed_reason == "timeout"
    assert time.perf_counter() - t0 < 10


def test_cancel_kills_whole_tree_including_grandchildren(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time, pathlib\n"
        f"p = subprocess.Popen([sys.executable, '-c', {SLEEPER!r}])\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    cancel = threading.Event()
    threading.Timer(1.5, cancel.set).start()
    r = _run(tmp_path, code, cancel=cancel)
    assert r.killed_reason == "cancelled"
    gpid = int(pidfile.read_text())
    time.sleep(0.5)
    assert not psutil.pid_exists(gpid) or psutil.Process(gpid).status() == psutil.STATUS_ZOMBIE


def test_memory_budget_kills_runaway(tmp_path: Path) -> None:
    code = "x = bytearray(400 * 1024 * 1024)\nimport time; time.sleep(30)"
    r = _run(tmp_path, code, max_rss_bytes=150 * 1024 * 1024)
    assert r.killed_reason == "memory"
    assert r.peak_rss_bytes > 150 * 1024 * 1024


def test_disk_budget_kills_runaway(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    code = (
        "import time, pathlib\n"
        f"pathlib.Path({str(ws / 'big.bin')!r}).write_bytes(b'0' * 3_000_000)\n"
        "time.sleep(30)\n"
    )
    r = _run(tmp_path, code, workspace=ws, max_workspace_bytes=1_000_000, tick_s=0.1)
    assert r.killed_reason == "disk"  # sampler runs every 5 s of elapsed time
