"""Concurrency races against the REAL Postgres of the live stack (PGlite serialises its one connection,
so unit tests cannot show these).

    python -m pytest tests/e2e/test_races.py -s
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image
from test_e2e import API, Tenant, stack_up

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not stack_up(), reason="local stack is not running")]


def noise_files(tmp: Path, n: int, seed: int) -> list[Path]:
    out = []
    for i in range(n):
        p = tmp / f"n{seed}_{i}.jpg"
        rng = np.random.default_rng(seed * 1000 + i)
        Image.fromarray(rng.integers(0, 256, (120, 160, 3), dtype=np.uint8)).save(p, quality=90)
        out.append(p)
    return out


def burst(n: int, fn):  # type: ignore[no-untyped-def]
    gate = threading.Barrier(n)

    def run(i: int):  # type: ignore[no-untyped-def]
        gate.wait()
        return fn(i)

    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(run, range(n)))


def test_job_queue_limit_holds_under_a_simultaneous_burst(tmp_path: Path) -> None:
    t = Tenant()
    _p, d = t.make_dataset(noise_files(tmp_path, 6, 1))
    statuses = burst(14, lambda _i: t.call("POST", f"/v1/datasets/{d}/jobs", json={}).status_code)
    assert statuses.count(202) == 5, statuses  # default limit: 5 queued or running jobs
    assert statuses.count(429) == 9, statuses


def test_duplicate_upload_requests_never_500(tmp_path: Path) -> None:
    t = Tenant()
    p = t.call("POST", "/v1/projects", json={"name": "r"}).json()
    d = t.call("POST", f"/v1/projects/{p['id']}/datasets", json={"name": "r"}).json()
    f = noise_files(tmp_path, 1, 2)[0]
    b = f.read_bytes()
    body = {"files": [{"filename": f.name, "contentType": "image/jpeg", "sizeBytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}]}
    results = burst(10, lambda _i: httpx.post(f"{API}/v1/datasets/{d['id']}/uploads", json=body, headers={"x-tenant-id": t.id}))
    codes = [r.status_code for r in results]
    assert set(codes) == {201}, codes
    actions = [r.json()["uploads"][0]["action"] for r in results]
    assert actions.count("UPLOAD") == 1 and actions.count("DUPLICATE") == 9, actions
    assert len({r.json()["uploads"][0]["imageId"] for r in results}) == 1
