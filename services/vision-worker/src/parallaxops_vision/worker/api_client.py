"""Worker -> API reporting. Events are idempotent by (job, attempt, seq), so retrying is always safe."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .settings import Settings


class ApiClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=settings.api_url,
            headers={"x-internal-secret": settings.internal_secret},
            timeout=httpx.Timeout(10.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, body: dict[str, Any], retries: int = 4) -> dict[str, Any]:
        delay = 0.5
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self._client.post(path, json=body)
                if r.status_code < 400:
                    return dict(r.json())
                if r.status_code < 500 and r.status_code != 429:
                    # Client errors will not succeed on retry (bad payload, wrong secret, unknown job).
                    raise httpx.HTTPStatusError(r.text[:300], request=r.request, response=r)
                last = httpx.HTTPStatusError(r.text[:300], request=r.request, response=r)
            except httpx.TransportError as exc:
                last = exc
            if attempt < retries:
                time.sleep(delay)
                delay = min(delay * 2, 8)
        assert last is not None
        raise last

    def event(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._post("/internal/v1/events", body)

    def preflight_result(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._post("/internal/v1/preflight", body)


class EventSender:
    """Per-attempt event stream with a monotonically increasing seq."""

    def __init__(self, api: ApiClient, job_id: str, attempt_no: int, worker_id: str) -> None:
        self.api, self.job_id, self.attempt_no, self.worker_id = api, job_id, attempt_no, worker_id
        self._seq = 0

    def send(self, type_: str, **fields: Any) -> dict[str, Any]:
        self._seq += 1
        body = {
            "jobId": self.job_id,
            "attemptNo": self.attempt_no,
            "seq": self._seq,
            "type": type_,
            "workerId": self.worker_id,
            "payload": fields.pop("payload", {}),
            **fields,
        }
        return self.api.event(body)
