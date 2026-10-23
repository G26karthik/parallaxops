# ADR 0002: Native-first local stack; all state outside the repo

Status: accepted (Gate 0)

## Context

Audit (`docs/ENVIRONMENT.md`): Docker's daemon is off, free RAM was 6.4 GiB because WSL2 and other
tools run concurrently, and the repository lives in a cloud-synced folder.

## Decision

- Local dev runs services as native processes started by `scripts/` (Postgres, S3-compatible store,
  Temporal dev server, API, worker, web). Docker Compose files are provided for the container path
  but are labelled **unverified** until executed.
- Everything persistent lives under `$PARALLAXOPS_HOME` (default `%LOCALAPPDATA%/ParallaxOps` or
  `~/.local/share/parallaxops`): venv, tools, datasets, Postgres data, object-store volumes,
  Temporal DB file, scratch. Temporal's dev server is always started with `--db-filename`, otherwise
  its state is in-memory and "durable" would be false.
- Database files are never placed in a synced folder.

## Consequences

- One command per service; `scripts/dev.ps1` orchestrates them on Windows.
- Setup downloads portable binaries into `$PARALLAXOPS_HOME/tools`; no global installs.
