from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path


def default_home() -> Path:
    env = os.environ.get("PARALLAXOPS_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "ParallaxOps"
    return Path.home() / ".local" / "share" / "parallaxops"


@dataclass(frozen=True)
class Settings:
    temporal_address: str = field(
        default_factory=lambda: os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    )
    temporal_namespace: str = field(
        default_factory=lambda: os.environ.get("TEMPORAL_NAMESPACE", "default")
    )
    api_url: str = field(default_factory=lambda: os.environ.get("API_URL", "http://127.0.0.1:4000"))
    internal_secret: str = field(
        default_factory=lambda: os.environ.get("INTERNAL_API_SECRET", "change-me-locally-please")
    )
    s3_endpoint: str = field(
        default_factory=lambda: os.environ.get("S3_ENDPOINT", "http://127.0.0.1:8333")
    )
    s3_bucket: str = field(default_factory=lambda: os.environ.get("S3_BUCKET", "parallaxops"))
    s3_region: str = field(default_factory=lambda: os.environ.get("S3_REGION", "us-east-1"))
    s3_access_key: str = field(
        default_factory=lambda: os.environ.get("S3_ACCESS_KEY", "dev-access-key")
    )
    s3_secret_key: str = field(
        default_factory=lambda: os.environ.get("S3_SECRET_KEY", "dev-secret-key-change-me-locally")
    )
    scratch_dir: Path = field(default_factory=lambda: default_home() / "scratch" / "jobs")
    metrics_port: int = field(
        default_factory=lambda: int(os.environ.get("WORKER_METRICS_PORT", "9108"))
    )
    #: Heavy slots per worker process. 1 = one concurrent CPU/GPU-heavy reconstruction.
    #: This is a per-worker cap; a fleet-wide cap would need a distributed lease (see docs).
    heavy_slots: int = field(default_factory=lambda: int(os.environ.get("WORKER_HEAVY_SLOTS", "1")))
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}")
