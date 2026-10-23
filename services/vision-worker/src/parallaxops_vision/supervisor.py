"""Run a subprocess under wall-clock, RSS and disk budgets with guaranteed process-tree teardown.

Windows: the child is placed in a Job Object with KILL_ON_JOB_CLOSE, so the whole tree dies even if
this process is killed. POSIX: the child gets its own session and the group is SIGKILLed.
Arguments are always an argv list; nothing is ever passed through a shell.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

if sys.platform == "win32":  # literal check so mypy/pyright analyse each OS correctly
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r_ops", "w_ops", "o_ops", "r_b", "w_b", "o_b")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _KILL_ON_CLOSE = 0x2000
    _JOB_MEMORY = 0x200
    _PROCESS_ALL = 0x1F0FFF

    class _Job:
        def __init__(self, memory_limit: int | None) -> None:
            self.handle = _k32.CreateJobObjectW(None, None)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
            info = _ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = _KILL_ON_CLOSE | (
                _JOB_MEMORY if memory_limit else 0
            )
            if memory_limit:
                info.JobMemoryLimit = memory_limit
            if not _k32.SetInformationJobObject(
                self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

        def assign(self, pid: int) -> None:
            h = _k32.OpenProcess(_PROCESS_ALL, False, pid)
            try:
                if not h or not _k32.AssignProcessToJobObject(self.handle, h):
                    raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
            finally:
                if h:
                    _k32.CloseHandle(h)

        def kill(self) -> None:
            _k32.TerminateJobObject(self.handle, 1)

        def close(self) -> None:
            if self.handle:
                _k32.CloseHandle(self.handle)
                self.handle = None


@dataclass
class SupervisedResult:
    returncode: int | None
    killed_reason: str | None  # None | timeout | cancelled | memory | disk
    wall_seconds: float
    peak_rss_bytes: int
    cpu_seconds: float
    log_tail: str = ""
    ticks: list[dict[str, Any]] = field(default_factory=list)


def _tree(proc: psutil.Process) -> list[psutil.Process]:
    try:
        return [proc, *proc.children(recursive=True)]
    except psutil.Error:
        return []


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, f))
    return total


def kill_tree(popen: subprocess.Popen[bytes], job: Any | None) -> None:
    if job is not None:
        job.kill()
    elif sys.platform != "win32":
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(popen.pid), signal.SIGKILL)
    with contextlib.suppress(psutil.Error):
        for p in _tree(psutil.Process(popen.pid))[::-1]:
            with contextlib.suppress(psutil.Error):
                p.kill()


def run_supervised(
    argv: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    timeout_s: float,
    max_rss_bytes: int,
    workspace: Path | None = None,
    max_workspace_bytes: int | None = None,
    cancel: threading.Event | None = None,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
    env: dict[str, str] | None = None,
    tick_s: float = 0.5,
) -> SupervisedResult:
    child_env = {**os.environ, "PYTHONUTF8": "1", **(env or {})}
    job: Any | None = None
    spawn_flags: dict[str, Any]
    if sys.platform == "win32":
        job = _Job(None)
        spawn_flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        spawn_flags = {"start_new_session": True}
    t0 = time.perf_counter()
    peak_rss = 0
    cpu_seen: dict[int, float] = {}
    killed: str | None = None
    ticks: list[dict[str, Any]] = []
    last_disk = 0.0
    disk_bytes = 0
    with log_path.open("wb") as log:
        popen: subprocess.Popen[bytes] = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=child_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **spawn_flags,
        )
        try:
            if job is not None:
                job.assign(popen.pid)
            ps = psutil.Process(popen.pid)
            while popen.poll() is None:
                time.sleep(tick_s)
                procs = _tree(ps)
                rss = 0
                for p in procs:
                    try:
                        rss += p.memory_info().rss
                        c = p.cpu_times()
                        cpu_seen[p.pid] = c.user + c.system
                    except psutil.Error:
                        pass
                peak_rss = max(peak_rss, rss)
                elapsed = time.perf_counter() - t0
                if workspace is not None and elapsed - last_disk >= 5:
                    disk_bytes, last_disk = _dir_bytes(workspace), elapsed
                tick = {"elapsed": round(elapsed, 2), "rss_bytes": rss, "disk_bytes": disk_bytes}
                ticks.append(tick)
                if on_tick:
                    on_tick(tick)
                if cancel is not None and cancel.is_set():
                    killed = "cancelled"
                elif elapsed > timeout_s:
                    killed = "timeout"
                elif rss > max_rss_bytes:
                    killed = "memory"
                elif max_workspace_bytes and disk_bytes > max_workspace_bytes:
                    killed = "disk"
                if killed:
                    kill_tree(popen, job)
                    break
        finally:
            if popen.poll() is None:
                kill_tree(popen, job)
            popen.wait()
            if job is not None:
                job.close()
    tail = ""
    try:
        data = log_path.read_bytes()
        tail = data[-4000:].decode("utf-8", errors="replace")
    except OSError:
        pass
    return SupervisedResult(
        returncode=popen.returncode,
        killed_reason=killed,
        wall_seconds=round(time.perf_counter() - t0, 3),
        peak_rss_bytes=peak_rss,
        cpu_seconds=round(sum(cpu_seen.values()), 2),
        log_tail=tail,
        ticks=ticks[-50:],
    )
