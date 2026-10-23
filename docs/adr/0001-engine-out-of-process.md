# ADR 0001: Run the SfM engine in a supervised child process

Status: accepted (Gate 1)

## Context

Cancellation must free CPU, RAM and the GPU, and kill any child processes. A pycolmap call
(C++ with its own thread pool) cannot be interrupted safely from inside the Python process that
hosts the workflow worker. Budgets (wall clock, RSS, disk) must be enforceable against a runaway job.

## Decision

`parallaxops_vision.engine` runs as `python -I -m parallaxops_vision.engine request.json` under
`supervisor.run_supervised`:

- argv list only, `shell=False`, never a user string; the `.bat` launcher of COLMAP is never used
  because Python routes `.bat` files through `cmd.exe`, which re-parses arguments.
- Windows: child assigned to a Job Object with `KILL_ON_JOB_CLOSE`; POSIX: own session +
  `killpg`; `psutil` tree kill as a second line.
- The supervisor samples process-tree RSS and workspace size and kills on budget breach.
- The pycolmap child also gives Linux CI and Docker workers for free via manylinux wheels.
- The optional GPU profile and dense MVS use the official COLMAP 4.2.1 CUDA CLI through the same
  supervisor (not yet wired; tracked in `STATE.md`).

## Consequences

- Process start cost (~1 s) per job: negligible against minutes of reconstruction.
- Progress is communicated by an append-only `progress.jsonl`, so heartbeats work across the boundary.
- pycolmap and the CLI share `database.db` and models and **must be pinned to the same version**;
  both versions are recorded in every manifest.
- Tests kill real process trees (`tests/test_supervisor.py`), including a grandchild process.
