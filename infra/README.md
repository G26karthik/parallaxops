# Infrastructure

| File | Purpose | Status |
|------|---------|--------|
| `../scripts/dev.ps1`, `../scripts/setup_tools.py` | Native local stack (Windows) | **Verified**: all e2e evidence was produced with it |
| `prometheus.yml` | Scrape config for the API and worker | Config only; not executed against a Prometheus binary |
| `docker-compose.yml`, `docker/*.Dockerfile` | Container path | **UNVERIFIED** until the table below says otherwise |

## Container verification log

Nothing here is claimed to work until it has been executed. Entries are added only from real runs.

| Date | Command | Result |
|------|---------|--------|
| _none yet_ | | |

## Notes

- The worker image is CPU-only (pycolmap from PyPI has no CUDA). GPU and dense reconstruction
  need the COLMAP CUDA build and the NVIDIA container runtime.
- `S3_PUBLIC_ENDPOINT` must be reachable by the browser (presigned URLs embed it); `S3_ENDPOINT`
  is the in-network address the API and worker use.
- Kubernetes manifests are deliberately absent: they will be added after the container path is
  verified, not before.
