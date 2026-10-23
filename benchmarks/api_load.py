"""Bounded closed-loop load test of the control-plane API, with and without a reconstruction running.

    scripts/dev.ps1 up
    python benchmarks/api_load.py

What this measures: HTTP latency and throughput of the Fastify API for a read-heavy mix, from
`--vus` concurrent virtual users on the SAME machine over loopback, with zero think time. What it
does NOT measure: GPU/CPU reconstruction throughput, network latency, or cloud-scale behaviour.
Concurrent HTTP requests are not concurrent reconstructions: exactly one reconstruction runs at a
time here. Results include the conditions so they cannot be misread.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import psutil

API = "http://127.0.0.1:4000"
OUT = Path(__file__).parent / "results" / "gate4" / "api_load.json"
MIX = [
    ("GET /v1/projects", 5),
    ("GET /v1/jobs/{id}", 5),
    ("GET /v1/projects/{id}/jobs", 2),
    ("POST /v1/projects", 1),
    ("GET /healthz", 1),
]


async def setup_tenant(c: httpx.AsyncClient, tenant: str) -> dict:
    h = {"x-tenant-id": tenant}
    p = (await c.post("/v1/projects", json={"name": "load"}, headers=h)).json()
    return {"headers": h, "project": p["id"]}


async def vu(
    c: httpx.AsyncClient,
    ctx: dict,
    job_id: str | None,
    stop: float,
    lat: dict[str, list[float]],
    errors: list[str],
) -> None:
    ops = [name for name, w in MIX for _ in range(w)]
    i = 0
    while time.perf_counter() < stop:
        name = ops[i % len(ops)]
        i += 1
        t0 = time.perf_counter()
        try:
            if name == "GET /v1/projects":
                r = await c.get("/v1/projects", headers=ctx["headers"])
            elif name == "GET /v1/jobs/{id}":
                if job_id is None:
                    continue
                r = await c.get(f"/v1/jobs/{job_id}", headers=ctx["headers"])
            elif name == "GET /v1/projects/{id}/jobs":
                r = await c.get(
                    f"/v1/projects/{ctx['project']}/jobs", headers=ctx["headers"]
                )
            elif name == "POST /v1/projects":
                r = await c.post(
                    "/v1/projects", json={"name": "p"}, headers=ctx["headers"]
                )
            else:
                r = await c.get("/healthz")
            ok = r.status_code < 400
        except httpx.HTTPError as e:
            ok, r = False, None
            errors.append(type(e).__name__)
        dt = (time.perf_counter() - t0) * 1000
        lat.setdefault(name, []).append(dt)
        if not ok:
            errors.append(
                f"{name} -> {r.status_code if r is not None else 'transport'}"
            )


async def run_phase(
    label: str, vus: int, seconds: float, job_id: str | None, tenant_job: dict | None
) -> dict:
    limits = httpx.Limits(max_connections=vus + 2, max_keepalive_connections=vus + 2)
    async with httpx.AsyncClient(base_url=API, limits=limits, timeout=15) as c:
        ctxs = [
            await setup_tenant(c, f"load-{uuid.uuid4().hex[:12]}") for _ in range(vus)
        ]
        if tenant_job:
            for x in ctxs:
                x["headers"] = tenant_job["headers"]  # all VUs read the one real job
        lat: dict[str, list[float]] = {}
        errors: list[str] = []
        await asyncio.gather(
            *(vu(c, x, job_id, time.perf_counter() + 2, {}, []) for x in ctxs)
        )  # warm-up
        start = time.perf_counter()
        await asyncio.gather(
            *(vu(c, x, job_id, start + seconds, lat, errors) for x in ctxs)
        )
        wall = time.perf_counter() - start
    allv = [v for vs in lat.values() for v in vs]
    pct = lambda a, q: round(float(np.percentile(a, q)), 2)  # noqa: E731
    return {
        "label": label,
        "virtual_users": vus,
        "duration_s": round(wall, 1),
        "requests": len(allv),
        "throughput_rps": round(len(allv) / wall, 1),
        "errors": len(errors),
        "error_samples": sorted(set(errors))[:5],
        "latency_ms": {
            "p50": pct(allv, 50),
            "p95": pct(allv, 95),
            "p99": pct(allv, 99),
            "max": round(max(allv), 1),
        },
        "per_endpoint_p95_ms": {k: pct(v, 95) for k, v in lat.items()},
    }


async def start_reconstruction() -> tuple[str, dict]:
    """Create a tenant, upload the downscaled terrace set, and start one real job."""
    import hashlib

    home = Path(
        __import__("os").environ.get("PARALLAXOPS_HOME")
        or Path(__import__("os").environ["LOCALAPPDATA"]) / "ParallaxOps"
    )
    src = home / "data" / "variants" / "terrace" / "clean"
    files = sorted(
        p for p in src.iterdir() if p.suffix.lower() == ".jpg"
    )  # case-insensitive, no duplicates
    tenant = f"load-job-{uuid.uuid4().hex[:10]}"
    h = {"x-tenant-id": tenant}
    async with httpx.AsyncClient(base_url=API, timeout=120) as c:
        p = (await c.post("/v1/projects", json={"name": "load-job"}, headers=h)).json()
        d = (
            await c.post(
                f"/v1/projects/{p['id']}/datasets", json={"name": "d"}, headers=h
            )
        ).json()
        meta = [
            {
                "filename": f.name,
                "contentType": "image/jpeg",
                "sizeBytes": f.stat().st_size,
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
            }
            for f in files
        ]
        up = (
            await c.post(
                f"/v1/datasets/{d['id']}/uploads", json={"files": meta}, headers=h
            )
        ).json()
        by = {f.name: f for f in files}
        for t in up["uploads"]:
            await httpx.AsyncClient().put(
                t["url"], content=by[t["filename"]].read_bytes(), headers=t["headers"]
            )
        await c.post(
            f"/v1/datasets/{d['id']}/uploads/complete",
            json={"imageIds": [u["imageId"] for u in up["uploads"]]},
            headers=h,
        )
        job = (
            await c.post(
                f"/v1/datasets/{d['id']}/jobs", json={"label": "load-test"}, headers=h
            )
        ).json()
        for _ in range(240):
            j = (await c.get(f"/v1/jobs/{job['id']}", headers=h)).json()
            if j["state"] == "RECONSTRUCTING":
                break
            await asyncio.sleep(0.5)
        return job["id"], {"headers": h, "state_at_start": j["state"]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--vus", type=int, nargs="*", default=[1, 8, 32])
    a = ap.parse_args()
    vm = psutil.virtual_memory()
    meta = {
        "at": datetime.now(UTC).isoformat(),
        "machine": {
            "logical_cores": psutil.cpu_count(),
            "ram_gib": round(vm.total / 1024**3, 1),
            "free_ram_gib_at_start": round(vm.available / 1024**3, 1),
        },
        "client": "httpx async, same host, loopback, closed loop, zero think time",
        "mix": {k: w for k, w in MIX},
        "note": "HTTP concurrency only. Exactly one reconstruction runs at a time; this is not reconstruction throughput.",
    }
    phases = []
    for n in a.vus:
        phases.append(
            {
                **await run_phase(f"idle, {n} VUs", n, a.seconds, None, None),
                "reconstruction_running": False,
            }
        )
    job_id, ctx = await start_reconstruction()
    meta["reconstruction_state_when_load_started"] = ctx["state_at_start"]
    for n in a.vus:
        phases.append(
            {
                **await run_phase(
                    f"during reconstruction, {n} VUs", n, a.seconds, job_id, ctx
                ),
                "reconstruction_running": True,
            }
        )
    OUT.write_text(
        json.dumps({"meta": meta, "phases": phases}, indent=2), encoding="utf-8"
    )
    for p in phases:
        print(
            p["label"],
            p["throughput_rps"],
            "rps",
            p["latency_ms"],
            "errors",
            p["errors"],
        )


if __name__ == "__main__":
    asyncio.run(main())
