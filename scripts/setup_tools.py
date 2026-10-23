"""Download the pinned local-stack binaries into $PARALLAXOPS_HOME/tools (Windows x64).

Idempotent: tools that already exist are skipped. Nothing is installed globally. Versions are
pinned here so a fresh clone reproduces the exact stack that the evidence in docs/ was measured on.

    python scripts/setup_tools.py            # install anything missing
    python scripts/setup_tools.py --check    # exit 1 if something is missing
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

NODE = "26.12.1"
SEAWEED = "4.49"
TEMPORAL = "1.9.1"
PG = "18.6.0"
ACTIONLINT = "1.7.12"

TOOLS = {
    "node": (
        f"https://nodejs.org/dist/v{NODE}/node-v{NODE}-win-x64.zip",
        f"node-v{NODE}-win-x64/node.exe",
    ),
    "seaweedfs": (
        f"https://github.com/seaweedfs/seaweedfs/releases/download/{SEAWEED}/windows_amd64.zip",
        "weed.exe",
    ),
    "temporal": (
        f"https://github.com/temporalio/cli/releases/download/v{TEMPORAL}/temporal_cli_{TEMPORAL}_windows_amd64.zip",
        "temporal.exe",
    ),
    "actionlint": (
        f"https://github.com/rhysd/actionlint/releases/download/v{ACTIONLINT}/actionlint_{ACTIONLINT}_windows_amd64.zip",
        "actionlint.exe",
    ),
}
PG_JAR = (
    "https://repo1.maven.org/maven2/io/zonky/test/postgres/"
    f"embedded-postgres-binaries-windows-amd64/{PG}/embedded-postgres-binaries-windows-amd64-{PG}.jar"
)


def home() -> Path:
    env = os.environ.get("PARALLAXOPS_HOME")
    if env:
        return Path(env)
    return Path(os.environ["LOCALAPPDATA"]) / "ParallaxOps"


def fetch(url: str) -> bytes:
    print(f"  downloading {url.rsplit('/', 1)[-1]} ...")
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - pinned https URLs
        return r.read()


def main() -> int:
    check = "--check" in sys.argv
    tools = home() / "tools"
    missing: list[str] = []

    def need(name: str, probe: Path) -> bool:
        if probe.exists():
            print(f"ok      {name}")
            return False
        missing.append(name)
        print(f"missing {name}")
        return not check

    for name, (url, probe_rel) in TOOLS.items():
        if need(name, tools / name / probe_rel):
            dest = tools / name
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(fetch(url))) as z:
                z.extractall(
                    dest
                )  # zipfile rejects absolute paths and .. traversal on extract
    if need("postgres", tools / "postgres" / "bin" / "postgres.exe"):
        with zipfile.ZipFile(io.BytesIO(fetch(PG_JAR))) as z:
            txz = next(n for n in z.namelist() if n.endswith(".txz"))
            data = z.read(txz)
        dest = tools / "postgres"
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
            tf.extractall(dest, filter="data")
    if need("pnpm", tools / "pnpm" / "pnpm.cmd"):
        node_dir = next((tools / "node").glob("node-*"))
        npm = shutil.which("npm", path=str(node_dir)) or str(node_dir / "npm.cmd")
        os.system(f'"{npm}" install -g pnpm@12.15.0 --prefix "{tools / "pnpm"}"')  # noqa: S605
    if check and missing:
        print(f"\nmissing tools: {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
