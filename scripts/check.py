"""The one CI definition. GitHub Actions and the local pre-push hook both run this script,
so a push that passes locally cannot fail CI for a reason this script covers.

    python scripts/check.py          # everything
    python scripts/check.py --fast   # skip the test suite (lint, types, workflow schema)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VISION = ROOT / "services" / "vision-worker"
PY = sys.executable


def tool_env() -> dict[str, str]:
    """Env for the TS steps. Uses the portable Node/pnpm under $PARALLAXOPS_HOME when present
    (local Windows dev); in CI Node/pnpm are already on PATH. The pnpm store lives outside the repo."""
    env = dict(os.environ)
    home = Path(
        env.get("PARALLAXOPS_HOME")
        or (
            Path(env["LOCALAPPDATA"]) / "ParallaxOps"
            if "LOCALAPPDATA" in env
            else Path.home() / ".local/share/parallaxops"
        )
    )
    env["PARALLAXOPS_HOME"] = str(home)
    extra = [
        str(p)
        for p in (
            home / "tools" / "pnpm",
            home / "tools" / "actionlint",
            *(home / "tools" / "node").glob("node-*"),
        )
        if p.exists()
    ]
    env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def ts_steps(fast: bool) -> list[tuple[str, list[str], Path]]:
    # A workspace without pnpm is a failure (main() reports it), never a quietly shorter check.
    if not (ROOT / "pnpm-workspace.yaml").exists():
        return []
    out = [
        ("biome (lint+format)", ["pnpm", "exec", "biome", "check", "."], ROOT),
        (
            "tsc (typecheck)",
            ["pnpm", "-r", "--workspace-concurrency=1", "typecheck"],
            ROOT,
        ),
    ]
    if not fast:
        out.append(("vitest (unit)", ["pnpm", "-r", "test"], ROOT))
        # A production build catches what tsc cannot: bundler, routing and config errors.
        out.append(
            (
                "next build (web)",
                ["pnpm", "--filter", "@parallaxops/web", "build"],
                ROOT,
            )
        )
    return out


def steps(fast: bool) -> list[tuple[str, list[str], Path]]:
    workflows = [str(p) for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))]
    out: list[tuple[str, list[str], Path]] = [
        (
            "workflow schema",
            [
                PY,
                "-m",
                "check_jsonschema",
                "--builtin-schema",
                "vendor.github-workflows",
                *workflows,
            ],
            ROOT,
        ),
        # actionlint also validates expression contexts (e.g. `runner` is not allowed in job env),
        # which the JSON schema cannot see. It is required: an unvalidated workflow is never pushed.
        ("actionlint", ["actionlint", "-no-color", *workflows], ROOT),
        ("ruff lint", [PY, "-m", "ruff", "check", "."], VISION),
        ("ruff format", [PY, "-m", "ruff", "format", "--check", "."], VISION),
    ]
    # mypy narrows on sys.platform, so each OS branch is checked separately (CI runs Linux).
    for plat in ("win32", "linux", "darwin"):
        out.append((f"mypy ({plat})", [PY, "-m", "mypy", "--platform", plat], VISION))
    if not fast:
        out.append(
            (
                "pytest (unit)",
                [PY, "-m", "pytest", "-q", "-m", "not integration and not gpu"],
                VISION,
            )
        )
    return [*out, *ts_steps(fast)]


def main() -> int:
    fast = "--fast" in sys.argv
    failed: list[str] = []
    env = tool_env()
    for name, cmd, cwd in steps(fast):
        t = time.perf_counter()
        exe = (
            shutil.which(cmd[0], path=env["PATH"])
            if cmd[0] in ("pnpm", "actionlint")
            else cmd[0]
        )
        if exe is None and cmd[0] in ("pnpm", "actionlint"):
            print(f"FAIL {name}: {cmd[0]} not installed. See docs/CI.md.")
            failed.append(name)
            continue
        res = subprocess.run(
            [exe or cmd[0], *cmd[1:]], cwd=cwd, env=env, capture_output=True, text=True
        )  # noqa: S603
        ok = res.returncode == 0
        print(f"{'ok  ' if ok else 'FAIL'} {name} ({time.perf_counter() - t:.1f}s)")
        if not ok:
            failed.append(name)
            print((res.stdout + res.stderr).strip()[-2500:])
    if failed:
        print(f"\n{len(failed)} step(s) failed: {', '.join(failed)}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
