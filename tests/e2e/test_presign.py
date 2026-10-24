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


HEADERS: dict[str, str] = {}


def presigned_upload() -> tuple[str, bytes]:
    t = Tenant()
    p = t.call("POST", "/v1/projects", json={"name": "p"}).json()
    d = t.call("POST", f"/v1/projects/{p['id']}/datasets", json={"name": "d"}).json()
    b = jpeg_bytes()
    body = {"files": [{"filename": "a.jpg", "contentType": "image/jpeg", "sizeBytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}]}
    up = t.call("POST", f"/v1/datasets/{d['id']}/uploads", json=body).json()["uploads"][0]
    assert up["action"] == "UPLOAD"
    HEADERS.clear()
    HEADERS.update(up["headers"])  # the headers the client must send, as the API returned them
    return up["url"], b


def put(url: str, data: bytes, content_type: str | None = None) -> int:
    headers = {**HEADERS, **({"content-type": content_type} if content_type else {})}
    return httpx.put(url, content=data, headers=headers).status_code


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


def test_declared_size_is_part_of_the_signature() -> None:
    """An upload larger or smaller than the declared size never reaches the bucket."""
    url, b = presigned_upload()
    assert put(url, b + b"\x00") == 403
    assert put(url, b[:-1]) == 403
    assert put(url, b) == 200


def test_digest_is_checked_by_the_store_and_cannot_be_dropped() -> None:
    """Same size, different bytes: the store answers 400 BadDigest. Without the header: 403."""
    url, b = presigned_upload()
    other = bytes([b[0] ^ 1]) + b[1:]
    assert put(url, other) == 400
    no_digest = {k: v for k, v in HEADERS.items() if k != "x-amz-checksum-sha256"}
    assert httpx.put(url, content=b, headers=no_digest).status_code == 403
    assert put(url, b) == 200


def test_content_type_is_part_of_the_signature() -> None:
    url, b = presigned_upload()
    assert put(url, b, content_type="text/html") == 403
