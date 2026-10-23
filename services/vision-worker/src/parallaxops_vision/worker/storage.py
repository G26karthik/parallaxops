"""S3-compatible object access for the worker. Workers share no disk: everything moves by key."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from pydantic import BaseModel

from .settings import Settings


class ManifestImage(BaseModel):
    imageId: str
    filename: str
    key: str
    sha256: str
    bytes: int
    contentType: str


class InputManifest(BaseModel):
    contract: int
    tenantId: str
    datasetId: str
    jobId: str | None
    outputPrefix: str
    images: list[ManifestImage]


class Store:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def get_json(self, key: str) -> Any:
        import json

        return json.loads(self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def download(self, key: str, dest: Path) -> str:
        """Download to dest and return the SHA-256 of what was written."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        with dest.open("wb") as f:
            for chunk in iter(lambda: body.read(1 << 20), b""):
                h.update(chunk)
                f.write(chunk)
        return h.hexdigest()

    def upload(self, path: Path, key: str, content_type: str) -> tuple[int, str]:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )
        return path.stat().st_size, h.hexdigest()
