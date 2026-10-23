"""The one CI definition. GitHub Actions and the local pre-push hook both run this script,
so a push that passes locally cannot fail CI for a reason this script covers.

    python scripts/check.py          # everything
    python scripts/check.py --fast   # skip the test suite (lint, types, workflow schema)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VISION = ROOT / "services" / "vision-worker"
PY = sys.executable


def steps(fast: bool) -> list[tuple[str, list[str], Path]]:
    workflows = [str(p) for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))]
    out: list[tuple[str, list[str], Path]] = [
        (
            "workflow schema",
            [PY, "-m", "check_jsonschema", "--builtin-schema", "vendor.github-workflows", *workflows],
            ROOT,
        ),
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
    return out


def main() -> int:
    fast = "--fast" in sys.argv
    failed: list[str] = []
    for name, cmd, cwd in steps(fast):
        t = time.perf_counter()
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)  # noqa: S603
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
