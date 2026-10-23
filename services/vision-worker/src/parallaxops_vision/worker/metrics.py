"""Prometheus metrics for the worker, including host and GPU load sampled at scrape time."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable

import psutil
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)
from prometheus_client.core import GaugeMetricFamily

REGISTRY = CollectorRegistry()
_LONG = (1, 5, 15, 30, 60, 120, 300, 600, 1200, 3600)

ACTIVITIES = Counter(
    "parallaxops_worker_activities_total",
    "Activities finished by outcome",
    ["activity", "outcome"],
    registry=REGISTRY,
)
STAGE_SECONDS = Histogram(
    "parallaxops_worker_stage_seconds",
    "Pipeline sub-stage duration",
    ["stage"],
    buckets=_LONG,
    registry=REGISTRY,
)
PEAK_RSS = Gauge(
    "parallaxops_worker_last_job_peak_rss_bytes",
    "Peak process-tree RSS of the last reconstruction",
    registry=REGISTRY,
)
ACTIVE_HEAVY = Gauge(
    "parallaxops_worker_active_heavy_jobs",
    "Heavy reconstructions running now",
    registry=REGISTRY,
)
ARTIFACT_BYTES = Counter(
    "parallaxops_worker_artifact_bytes_total",
    "Bytes published to object storage",
    registry=REGISTRY,
)
CLEANUP_BYTES = Counter(
    "parallaxops_worker_scratch_cleaned_bytes_total",
    "Scratch bytes removed after attempts (success, failure or cancel)",
    ["outcome"],
    registry=REGISTRY,
)


class _HostCollector:
    """CPU, RAM and (if present) NVIDIA GPU load. GPU values are cached for a few seconds."""

    def __init__(self) -> None:
        self._gpu: tuple[float, list[tuple[float, float, float]]] = (0.0, [])

    def _query_gpu(self) -> list[tuple[float, float, float]]:
        now = time.monotonic()
        if now - self._gpu[0] < 5:
            return self._gpu[1]
        rows: list[tuple[float, float, float]] = []
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
            for line in out.strip().splitlines():
                u, used, total = (float(x) for x in line.split(","))
                rows.append((u, used, total))
        except (OSError, subprocess.SubprocessError, ValueError):
            rows = []
        self._gpu = (now, rows)
        return rows

    def collect(self) -> Iterable[GaugeMetricFamily]:
        cpu = GaugeMetricFamily("parallaxops_host_cpu_percent", "Host CPU utilisation")
        cpu.add_metric([], psutil.cpu_percent(interval=None))
        yield cpu
        mem = GaugeMetricFamily("parallaxops_host_memory_available_bytes", "Host RAM available")
        mem.add_metric([], float(psutil.virtual_memory().available))
        yield mem
        util = GaugeMetricFamily(
            "parallaxops_gpu_utilization_percent", "GPU utilisation", labels=["gpu"]
        )
        used = GaugeMetricFamily("parallaxops_gpu_memory_used_mib", "GPU VRAM used", labels=["gpu"])
        for i, (u, m, _total) in enumerate(self._query_gpu()):
            util.add_metric([str(i)], u)
            used.add_metric([str(i)], m)
        yield util
        yield used


REGISTRY.register(_HostCollector())


def serve(port: int) -> None:
    start_http_server(port, registry=REGISTRY, addr="127.0.0.1")
