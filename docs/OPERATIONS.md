# Operations

## Run the stack (Windows, no Docker)

```powershell
.\scripts\dev.ps1 setup     # tools, Python 3.14 venv, pnpm install, Postgres init (idempotent)
.\scripts\dev.ps1 up        # Postgres, S3 (SeaweedFS), Temporal, API, worker, web
.\scripts\dev.ps1 status
.\scripts\dev.ps1 logs api  # also: worker, web, temporal, s3
.\scripts\dev.ps1 down
```

Ports: web 3000, API 4000 (`/docs`, `/openapi.json`, `/metrics`, `/readyz`), Temporal 7233 (UI
8233 if enabled), S3 8333, Postgres 5433, worker metrics 9108. State lives under
`%LOCALAPPDATA%\ParallaxOps` (never in the repo).

## Health

- `GET /healthz` liveness. `GET /readyz` checks Postgres, the object store and the workflow engine
  and returns 503 with the failing component. The web header shows the same status.

## Metrics (Prometheus)

API (`:4000/metrics`): `parallaxops_http_request_duration_seconds{method,route,status}`,
`parallaxops_jobs_created_total`, `parallaxops_job_transitions_total{to}`,
`parallaxops_job_duration_seconds{outcome}`, `parallaxops_job_queue_wait_seconds`,
`parallaxops_stage_duration_seconds{stage}`, `parallaxops_artifact_bytes{kind}`,
`parallaxops_jobs_active{state}`, `parallaxops_upload_rejected_total{reason}`,
`parallaxops_workflow_start_failures_total`, `parallaxops_cleanup_removed_total{kind}`, plus Node
process metrics.

Worker (`:9108/metrics`): `parallaxops_worker_activities_total{activity,outcome}`,
`parallaxops_worker_stage_seconds{stage}`, `parallaxops_worker_active_heavy_jobs`,
`parallaxops_worker_last_job_peak_rss_bytes`, `parallaxops_worker_artifact_bytes_total`,
`parallaxops_worker_scratch_cleaned_bytes_total{outcome}`, and host load sampled at scrape time:
`parallaxops_host_cpu_percent`, `parallaxops_host_memory_available_bytes`,
`parallaxops_gpu_utilization_percent{gpu}`, `parallaxops_gpu_memory_used_mib{gpu}` (empty when no
NVIDIA GPU is visible). A sample scrape config is in `infra/prometheus.yml`.

## Tracing and logs

Logs are structured JSON (Fastify/pino) with a request id that is also returned to callers in
`x-request-id` and every error body. The job id correlates API logs, Temporal workflow history and
worker logs. Full OpenTelemetry traces are not implemented.

## Runbook

| Symptom | Check |
|---------|-------|
| Job stuck in `QUEUED` | Worker up? (`dev.ps1 status`). Temporal reachable? `/readyz`. Another job holding the single heavy slot? The API reconciler also restarts workflows that never started (every 10 s). |
| Job `FAILED` with `ENGINE_CRASH` after 3 attempts | `logs/engine.log` artifact; free RAM (the engine is budgeted to 14 GiB RSS). |
| Upload rejected `checksum_mismatch` | The bytes changed in transit; retry the upload. |
| `/readyz` shows `objectStore: down` | SeaweedFS process and `S3_ENDPOINT`; bucket is created on API boot. |
| Disk filling | `scratch/jobs` should be empty between jobs; the API janitor removes abandoned uploads (60 min) and expired artifacts (30 days). |

## Capacity expectations (this laptop, measured)

One 23-image, 1600 px reconstruction: about 1.5 minutes, 3.8 GiB peak RSS, CPU only. Free RAM,
not installed RAM, is the binding limit: other tools on the machine routinely held more than half.
