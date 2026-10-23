"""Download and extract an ETH3D DSLR scene into $PARALLAXOPS_HOME/data (never into the repo).

    python scripts/fetch_dataset.py terrace

The data is NOT redistributed by this project; see docs/DATASETS.md for provenance and the license
caveat. Requires `py7zr` (installed with the `dev` extra).
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

SCENES = {  # scene -> archive size in bytes (from the host's Content-Range header)
    "terrace": 233_760_734,
    "relief": 287_532_557,
    "pipes": 109_668_393,
}
URL = "https://www.eth3d.net/data/{scene}_dslr_jpg.7z"


def home() -> Path:
    env = os.environ.get("PARALLAXOPS_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "ParallaxOps"
    return Path.home() / ".local" / "share" / "parallaxops"


def main(scene: str) -> Path:
    if scene not in SCENES:
        raise SystemExit(f"unknown scene {scene!r}; choose from {sorted(SCENES)}")
    raw = home() / "data" / "raw" / "eth3d"
    raw.mkdir(parents=True, exist_ok=True)
    archive = raw / f"{scene}_dslr_jpg.7z"
    if not archive.exists() or archive.stat().st_size != SCENES[scene]:
        print(f"downloading {URL.format(scene=scene)} ...")
        urllib.request.urlretrieve(URL.format(scene=scene), archive)  # noqa: S310 - fixed https URL
    if archive.stat().st_size != SCENES[scene]:
        raise SystemExit("download size mismatch; delete the file and retry")
    out = home() / "data" / "eth3d" / scene
    if not (out / scene / "images").exists():
        import py7zr

        out.mkdir(parents=True, exist_ok=True)
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(out)  # py7zr rejects path traversal entries
    print(f"ready: {out / scene}")
    return out / scene


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "terrace")
