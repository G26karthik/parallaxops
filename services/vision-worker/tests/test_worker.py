from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import open3d as o3d
import pytest
from temporalio.exceptions import ApplicationError

from parallaxops_vision.worker import activities
from parallaxops_vision.worker.api_client import ApiClient, EventSender
from parallaxops_vision.worker.artifacts import PREVIEW_MAGIC, build_preview
from parallaxops_vision.worker.settings import Settings
from parallaxops_vision.worker.storage import InputManifest


def client(handler: Any) -> ApiClient:
    return ApiClient(Settings(api_url="http://api.test"), transport=httpx.MockTransport(handler))


def test_events_carry_secret_and_monotonic_seq() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"applied": True})

    sender = EventSender(client(handler), "job-1", 2, "w1")
    sender.send("attempt_started")
    sender.send("heartbeat", payload={"rssBytes": 1})
    bodies = [json.loads(r.content) for r in seen]
    assert [b["seq"] for b in bodies] == [1, 2]
    assert {b["attemptNo"] for b in bodies} == {2}
    assert seen[0].headers["x-internal-secret"]
    assert bodies[1]["payload"] == {"rssBytes": 1}


def test_api_client_retries_transient_failures_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": 1})

    assert client(handler).event({"x": 1}) == {"ok": 1}
    assert calls["n"] == 3


def test_api_client_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="nope")

    with pytest.raises(httpx.HTTPStatusError):
        client(handler).event({"x": 1})
    assert calls["n"] == 1


def manifest(
    tenant: str = "tenant-aaaaaaaa", dataset: str = "d1", key: str | None = None
) -> dict[str, Any]:
    return {
        "contract": 1,
        "tenantId": tenant,
        "datasetId": dataset,
        "jobId": "j1",
        "outputPrefix": "x/",
        "images": [
            {
                "imageId": "i1",
                "filename": "a.jpg",
                "key": key or f"tenants/{tenant}/datasets/{dataset}/images/i1.jpg",
                "sha256": "0" * 64,
                "bytes": 1,
                "contentType": "image/jpeg",
            }
        ],
    }


class FakeStore:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc

    def get_json(self, _key: str) -> dict[str, Any]:
        return self.doc


def inp(**kw: Any) -> dict[str, Any]:
    return {
        "contract": 1,
        "tenantId": "tenant-aaaaaaaa",
        "datasetId": "d1",
        "inputManifestKey": "m",
        **kw,
    }


def test_manifest_accepts_own_tenant_prefix() -> None:
    m = activities.load_manifest(FakeStore(manifest()), inp())  # type: ignore[arg-type]
    assert isinstance(m, InputManifest) and len(m.images) == 1


@pytest.mark.parametrize(
    "doc",
    [
        manifest(
            key="tenants/other-tenant-bbbb/datasets/d1/images/i1.jpg"
        ),  # another tenant's object
        manifest(key="tenants/tenant-aaaaaaaa/datasets/d1/images/../../../x.jpg"),  # traversal
        manifest(dataset="d2"),  # manifest for a different dataset than the workflow input
        {**manifest(), "contract": 2},  # unknown contract major
    ],
)
def test_manifest_guard_rejects_cross_tenant_and_skew(doc: dict[str, Any]) -> None:
    with pytest.raises(ApplicationError) as e:
        activities.load_manifest(FakeStore(doc), inp())  # type: ignore[arg-type]
    assert e.value.non_retryable


def test_unknown_workflow_contract_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as e:
        activities.check_contract({"contract": 99})
    assert e.value.non_retryable and e.value.type == "ContractError"


def test_config_mapping_from_api_camel_case() -> None:
    cfg = activities.to_config(
        {
            "maxImageSize": 1200,
            "maxNumFeatures": 4096,
            "mapper": "global",
            "cameraModel": "OPENCV",
            "seed": 7,
        }
    )
    assert (cfg.max_image_size, cfg.max_num_features, cfg.mapper, cfg.camera_model, cfg.seed) == (
        1200,
        4096,
        "global",
        "OPENCV",
        7,
    )


def test_safe_name_keeps_order_and_strips_hostile_characters() -> None:
    assert activities.safe_name(3, "../../etc/pass wd.jpg") == "0003_pass_wd.jpg"
    assert activities.safe_name(12, "") == "0012_image"


def test_preview_format_roundtrip_and_decimation(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(rng.normal(size=(1000, 3)))
    pcd.colors = o3d.utility.Vector3dVector(rng.uniform(size=(1000, 3)))
    ply = tmp_path / "p.ply"
    o3d.io.write_point_cloud(ply, pcd)

    out = tmp_path / "p.bin"
    n = build_preview(ply, out)
    raw = out.read_bytes()
    magic, version, count, _ = struct.unpack("<4sIII", raw[:16])
    assert (magic, version) == (PREVIEW_MAGIC, 1)
    assert count == n == 1000
    xyz = np.frombuffer(raw[16 : 16 + count * 12], dtype="<f4").reshape(-1, 3)
    assert np.allclose(xyz, np.asarray(pcd.points), atol=1e-5)
    assert len(raw) == 16 + count * 12 + count * 3

    small = tmp_path / "s.bin"
    assert build_preview(ply, small, max_points=200) <= 200  # decimated, deterministic
    assert build_preview(ply, tmp_path / "s2.bin", max_points=200) == build_preview(
        ply, small, max_points=200
    )
