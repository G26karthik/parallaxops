"""Screenshot a completed dense job in the real UI (dev helper; writes docs/assets/ui/09-dense*.png).

    python tests/e2e/shots_dense.py <tenant-id> <job-id>

The tenant id is set in the browser's localStorage so the app shows that tenant's job.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

HOME = Path(
    os.environ.get("PARALLAXOPS_HOME")
    or Path(os.environ["LOCALAPPDATA"]) / "ParallaxOps"
)
OUT = Path(__file__).resolve().parents[2] / "docs" / "assets" / "ui"
tenant, job = sys.argv[1], sys.argv[2]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(HOME / "tools" / "playwright"))

with sync_playwright() as pw:
    b = pw.chromium.launch(
        args=[
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--ignore-gpu-blocklist",
        ]
    )
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
    ctx.add_init_script(
        f"window.localStorage.setItem('parallaxops.tenant', '{tenant}')"
    )
    page = ctx.new_page()
    page.goto(f"http://127.0.0.1:3000/jobs/{job}")
    expect(page.locator("main h1 span[aria-live]")).to_contain_text(
        "COMPLETED", timeout=30_000
    )
    expect(page.locator("canvas")).to_be_visible(timeout=60_000)
    page.get_by_role("radio", name="Dense MVS", exact=False).check()
    page.wait_for_timeout(4000)
    page.locator("canvas").screenshot(path=str(OUT / "09-dense-viewer.png"))
    page.screenshot(path=str(OUT / "10-dense-job.png"), full_page=True)
    b.close()
print("saved")
