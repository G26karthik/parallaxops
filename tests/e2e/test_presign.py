"""Presigned upload URLs against the REAL object store (SeaweedFS S3) of the live stack.

    python -m pytest tests/e2e/test_presign.py -s

Expiry is not tested here: the TTL is 15 minutes and is a constant of the API, not a knob.
"""

from __future__ import annotations

import hashlib
import io

import httpx
import pytest
from PIL import Image
from test_e2e import Tenant, stack_up

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not stack_up(), reason="local stack is not running")]


def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


def presigned_upload() -> tuple[str, bytes]:
    t = Tenant()
    p = t.call("POST", "/v1/projects", json={"name": "p"}).json()
    d = t.call("POST", f"/v1/projects/{p['id']}/datasets", json={"name": "d"}).json()
    b = jpeg_bytes()
    body = {"files": [{"filename": "a.jpg", "contentType": "image/jpeg", "sizeBytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}]}
    up = t.call("POST", f"/v1/datasets/{d['id']}/uploads", json=body).json()["uploads"][0]
    assert up["action"] == "UPLOAD"
    return up["url"], b


def put(url: str, data: bytes, content_type: str = "image/jpeg") -> int:
    return httpx.put(url, content=data, headers={"content-type": content_type}).status_code


def test_valid_presigned_put_is_accepted() -> None:
    url, b = presigned_upload()
    assert put(url, b) == 200


def test_tampered_signature_is_rejected() -> None:
    url, b = presigned_upload()
    sig = "X-Amz-Signature="
    i = url.index(sig) + len(sig)
    flipped = url[:i] + ("0" if url[i] != "0" else "1") + url[i + 1 :]
    assert put(flipped, b) == 403


def test_url_cannot_be_reused_for_another_key() -> None:
    url, b = presigned_upload()
    path_start = url.index("/", url.index("//") + 2)
    other = url[:path_start] + "/x" + url[path_start:]
    assert put(other, b) == 403


def test_content_type_is_part_of_the_signature() -> None:
    url, b = presigned_upload()
    assert put(url, b, content_type="text/html") == 403
