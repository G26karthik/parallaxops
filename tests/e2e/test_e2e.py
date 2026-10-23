"""End-to-end tests against the REAL local stack (Postgres, S3, Temporal, API, worker).

    scripts/dev.ps1 up
    python -m pytest tests/e2e -m e2e -s

Not run in CI: they need the services and the ETH3D dataset. Evidence is written to
benchmarks/results/gate2-e2e/.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

API = os.environ.get("API_URL", "http://127.0.0.1:4000")
HOME = Path(os.environ.get("PARALLAXOPS_HOME") or Path(os.environ.get("LOCALAPPDATA", "")) / "ParallaxOps")
TERRACE = HOME / "data" / "eth3d" / "terrace" / "terrace" / "images" / "dslr_images"
EVIDENCE = Path(__file__).resolve().parents[2] / "benchmarks" / "results" / "gate2-e2e"
ACTIVE = {"PREPROCESSING", "RECONSTRUCTING", "EVALUATING"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def stack_up() -> bool:
    try:
        return httpx.get(f"{API}/readyz", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not stack_up(), reason="local stack is not running")]


class Tenant:
    def __init__(self) -> None:
        self.id = f"e2e-{uuid.uuid4().hex[:16]}"
        self.http = httpx.Client(base_url=API, headers={"x-tenant-id": self.id}, timeout=30)

    def call(self, method: str, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.http.request(method, url, **kw)

    def make_dataset(self, files: list[Path]) -> tuple[str, str]:
        p = self.call("POST", "/v1/projects", json={"name": "e2e"}).json()
        d = self.call("POST", f"/v1/projects/{p['id']}/datasets", json={"name": "scene"}).json()
        meta = []
        for f in files:
            b = f.read_bytes()
            ct = "image/png" if f.suffix.lower() == ".png" else "image/jpeg"
            meta.append({"filename": f.name, "contentType": ct, "sizeBytes": len(b), "sha256": hashlib.sha256(b).hexdigest()})
        up = self.call("POST", f"/v1/datasets/{d['id']}/uploads", json={"files": meta})
        assert up.status_code == 201, up.text
        by_name = {f.name: f for f in files}
        for target in up.json()["uploads"]:
            assert target["action"] == "UPLOAD"
            r = httpx.put(target["url"], content=by_name[target["filename"]].read_bytes(), headers=target["headers"], timeout=120)
            assert r.status_code == 200, r.text
        done = self.call("POST", f"/v1/datasets/{d['id']}/uploads/complete", json={"imageIds": [u["imageId"] for u in up.json()["uploads"]]})
        assert all(i["status"] == "UPLOADED" for i in done.json()["images"]), done.text
        return p["id"], d["id"]

    def wait(self, job_id: str, until: set[str], timeout: float = 600) -> tuple[dict, list[tuple[float, str]]]:
        t0, seen = time.perf_counter(), []
        while time.perf_counter() - t0 < timeout:
            j = self.call("GET", f"/v1/jobs/{job_id}").json()
            if not seen or seen[-1][1] != j["state"]:
                seen.append((round(time.perf_counter() - t0, 1), j["state"]))
            if j["state"] in until:
                return j, seen
            time.sleep(0.5)
        raise AssertionError(f"timeout; states seen: {seen}")


@pytest.fixture(scope="module")
def terrace_images() -> list[Path]:
    if not TERRACE.is_dir():
        pytest.skip("ETH3D terrace not downloaded (python scripts/fetch_dataset.py terrace)")
    return sorted(TERRACE.glob("*.JPG"))


@pytest.fixture(scope="module")
def scene(terrace_images: list[Path]):  # type: ignore[no-untyped-def]
    t = Tenant()
    project_id, dataset_id = t.make_dataset(terrace_images)
    return t, project_id, dataset_id


def save(name: str, obj: object) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(json.dumps(obj, indent=2), encoding="utf-8")


def test_stack_is_ready() -> None:
    r = httpx.get(f"{API}/readyz")
    assert r.status_code == 200, r.text
    assert r.json()["components"] == {"database": "ok", "objectStore": "ok", "workflowEngine": "ok"}


def test_preflight_runs_qc_in_the_worker(scene) -> None:  # type: ignore[no-untyped-def]
    t, _p, d = scene
    r = t.call("POST", f"/v1/datasets/{d}/preflight")
    assert r.status_code == 202
    for _ in range(120):
        ds = t.call("GET", f"/v1/datasets/{d}").json()
        if ds["preflight"]["status"] in {"DONE", "FAILED"}:
            break
        time.sleep(1)
    assert ds["preflight"]["status"] == "DONE", ds
    rep = ds["preflight"]["report"]
    assert len(rep["images"]) == len(list(TERRACE.glob("*.JPG")))
    assert rep["images"][0]["name"].endswith(".JPG") and not rep["images"][0]["name"][:4].isdigit()
    assert "heuristic" in rep["note"].lower()
    save("preflight_report_summary.json", {"n_images": len(rep["images"]), "warnings": rep["warnings"], "total_megapixels": rep["total_megapixels"]})


def test_full_reconstruction_job_produces_real_artifacts(scene) -> None:  # type: ignore[no-untyped-def]
    t, _p, d = scene
    key = {"idempotency-key": f"e2e-{uuid.uuid4()}"}
    t0 = time.perf_counter()
    first = t.call("POST", f"/v1/datasets/{d}/jobs", json={"label": "e2e baseline"}, headers=key)
    submit_ms = (time.perf_counter() - t0) * 1000
    assert first.status_code == 202 and submit_ms < 2000
    job_id = first.json()["id"]
    replay = t.call("POST", f"/v1/datasets/{d}/jobs", json={"label": "e2e baseline"}, headers=key)
    assert replay.status_code == 200 and replay.json()["id"] == job_id and replay.json()["idempotentReplay"]

    job, seen = t.wait(job_id, TERMINAL)
    assert job["state"] == "COMPLETED", job.get("diagnostic")
    states = [s for _, s in seen]
    assert states[-1] == "COMPLETED"
    assert {"RECONSTRUCTING", "EVALUATING"} <= set(states)

    m = job["metrics"]
    assert m["n_registered"] >= 0.9 * m["n_input_images"] and m["n_points3d"] > 1000
    assert job["quality"]["level"] in {"good", "fair", "poor"}
    stage_names = {s["name"] for s in job["stages"]}
    assert {"qc", "feature_extraction", "feature_matching", "sparse_mapping", "metrics", "export"} <= stage_names
    assert job["attempts"][0]["state"] == "SUCCEEDED" and job["attempts"][0]["workerId"]

    # artifacts: presigned links resolve and checksums match what the worker recorded
    arts = {a["kind"]: a for a in t.call("GET", f"/v1/jobs/{job_id}/artifacts").json()}
    assert {"points_ply", "points_preview", "cameras", "report", "manifest", "colmap_sparse"} <= set(arts)
    ply = httpx.get(arts["points_ply"]["url"], timeout=60)
    assert ply.status_code == 200 and hashlib.sha256(ply.content).hexdigest() == arts["points_ply"]["sha256"]
    assert ply.content.startswith(b"ply")
    prev = httpx.get(arts["points_preview"]["url"], timeout=60).content
    magic, version, count, _ = struct.unpack("<4sIII", prev[:16])
    assert magic == b"PXPT" and version == 1 and count == m["n_points3d"]
    assert len(prev) == 16 + count * 3 * 4 + count * 3
    cams = httpx.get(arts["cameras"]["url"], timeout=30).json()
    assert len(cams["images"]) == m["n_registered"]
    scene_info = t.call("GET", f"/v1/jobs/{job_id}/scene").json()
    assert scene_info["pointCount"] == m["n_points3d"]

    # A second tenant can neither read the job nor use its presigned artifact prefix via the API
    other = Tenant()
    assert other.call("GET", f"/v1/jobs/{job_id}").status_code == 404
    assert other.call("GET", f"/v1/jobs/{job_id}/artifacts").status_code == 404

    save("completed_job.json", {"submit_ms": round(submit_ms, 1), "timeline": seen, "job": job})


def test_failed_job_reports_actionable_diagnostics(tmp_path: Path) -> None:
    import numpy as np
    from PIL import Image

    files = []
    for i in range(6):
        rng = np.random.default_rng(i)
        p = tmp_path / f"noise_{i}.jpg"
        Image.fromarray(rng.integers(0, 256, (360, 480, 3), dtype=np.uint8)).save(p, quality=95)
        files.append(p)
    t = Tenant()
    _p, d = t.make_dataset(files)
    job_id = t.call("POST", f"/v1/datasets/{d}/jobs", json={"config": {"maxImageSize": 480}}).json()["id"]
    job, seen = t.wait(job_id, TERMINAL, timeout=300)
    assert job["state"] == "FAILED"
    diag = job["diagnostic"]
    assert diag["code"] in {"NO_FEATURES", "INSUFFICIENT_MATCHES", "SFM_NO_MODEL", "NO_INITIAL_PAIR", "LOW_REGISTRATION"}
    assert diag["advice"] and diag["message"]
    save("failed_job.json", {"timeline": seen, "diagnostic": diag})


def test_second_job_queues_behind_the_single_heavy_slot(scene) -> None:  # type: ignore[no-untyped-def]
    t, _p, d = scene
    a = t.call("POST", f"/v1/datasets/{d}/jobs", json={"label": "A"}).json()["id"]
    b = t.call("POST", f"/v1/datasets/{d}/jobs", json={"label": "B"}).json()["id"]
    assert a != b
    max_active, b_waited = 0, False
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 900:
        ja = t.call("GET", f"/v1/jobs/{a}").json()
        jb = t.call("GET", f"/v1/jobs/{b}").json()
        active = sum(1 for j in (ja, jb) if j["state"] in ACTIVE)
        max_active = max(max_active, active)
        if ja["state"] in ACTIVE and jb["state"] == "QUEUED":
            b_waited = True
        if ja["state"] in TERMINAL and jb["state"] in TERMINAL:
            break
        time.sleep(0.5)
    assert max_active == 1, "two heavy jobs ran concurrently"
    assert b_waited, "second job never observed waiting in QUEUED while the first ran"
    assert ja["state"] == jb["state"] == "COMPLETED"
    assert jb["queueWaitSeconds"] > 5  # it really waited for the first
    save("queueing.json", {"queue_wait_seconds_B": jb["queueWaitSeconds"], "max_concurrent_active": max_active})


def test_cancel_running_job_stops_engine_and_frees_slot(scene) -> None:  # type: ignore[no-untyped-def]
    t, _p, d = scene
    job_id = t.call("POST", f"/v1/datasets/{d}/jobs", json={"label": "to-cancel"}).json()["id"]
    j, _ = t.wait(job_id, {"RECONSTRUCTING"}, timeout=120)
    time.sleep(3)  # let the engine child process spin up
    assert t.call("POST", f"/v1/jobs/{job_id}/cancel").status_code == 202
    j, seen = t.wait(job_id, TERMINAL, timeout=120)
    assert j["state"] == "CANCELLED", seen
    assert j["attempts"][-1]["state"] == "CANCELLED"
    # the slot is free again: a fresh job starts promptly
    nxt = t.call("POST", f"/v1/datasets/{d}/jobs", json={"label": "after-cancel"}).json()["id"]
    j2, _ = t.wait(nxt, {"PREPROCESSING", "RECONSTRUCTING", "COMPLETED"}, timeout=120)
    t.call("POST", f"/v1/jobs/{nxt}/cancel")
    save("cancel.json", {"cancelled_job_timeline": seen})
