# Environment audit (Gate 0)

Measured on the development machine on 2026-10-24. Machine-specific paths, accounts and process
lists are intentionally omitted.

## Host

| Item | Finding |
|------|---------|
| OS | Windows 11 Home (build 26200), x64 |
| CPU | AMD Ryzen 9 8945HS, 8 cores / 16 threads |
| RAM | 31.2 GiB installed. **Free RAM at audit time: 6.4 GiB**, because WSL2 and other tools run concurrently |
| GPU | NVIDIA GeForce RTX 4060 Laptop, 8188 MiB VRAM, driver 617.42, CUDA driver API 13.4 |
| Disk | 215 GiB free on the system drive |
| WSL2 | Ubuntu available (state: running); a `docker-desktop` distro exists (stopped) |
| Docker | Client 29.7.2 installed; **daemon not running** at audit time |
| Node / pnpm | Node 22.23 (LTS) installed system-wide, pnpm 10.29 |
| Python | 3.11 and 3.13 system-wide; **3.14.2 used** via `uv` in an isolated venv |
| Git / GitHub CLI | git 2.55, gh 2.100, authenticated |
| `nvcc`, `cmake`, `colmap`, `make` | not installed |

## Consequences and decisions

1. **Free RAM is the binding constraint**, not installed RAM. The supervisor enforces a configurable
   RSS budget (default 14 GiB) and the platform runs one heavy job at a time.
2. **Native-first local stack.** Docker's daemon is off and a Compose-only path would add several GiB of
   RAM. Services run as native processes; Compose files are provided for the Docker path but only
   claimed as working after they have been executed (see `infra/README.md`).
3. **pycolmap 4.2.1 from PyPI is CPU-only here** (`pycolmap.has_cuda == False`; verified). The CPU
   path is the default and is what Gates 1-4 measure. GPU acceleration and dense MVS use the
   official COLMAP 4.2.1 Windows **CUDA** binary release as a separate, optional profile.
4. **All persistent state lives outside the repository** under `$PARALLAXOPS_HOME`
   (`%LOCALAPPDATA%\ParallaxOps` on Windows): virtualenv, downloaded tools, datasets, database
   files, object-store data, Temporal state and scratch. The repo sits in a cloud-synced folder on this
   machine, and database files must never be synced.
5. No change to Windows power, display-timeout or registry settings is made or required.

## Toolchain versions in use

| Component | Version |
|-----------|---------|
| Python | 3.14.2 |
| pycolmap | 4.2.1 (`has_cuda = False`) |
| COLMAP CLI (optional GPU / dense profile) | 4.2.1 `colmap-x64-windows-cuda` |
| Open3D | 0.20.0 |
| OpenCV | 5.0.0 |
| NumPy | 2.5.4 |
| temporalio (Python SDK) | 1.34.0 |
| Temporal CLI (dev server) | 1.9.1 |
| ruff / mypy / pytest | 0.17.2 / 2.4.0 / 9.1.1 |

## Verified vs. not yet verified

- Verified: hardware inventory above, CPU SfM end to end, GPU reported by `nvidia-smi`.
- Not yet verified on this machine: COLMAP CUDA binary execution, Docker Compose services.
  Status of each is tracked in `STATE.md` and the gate evidence in `docs/GATES.md`.
