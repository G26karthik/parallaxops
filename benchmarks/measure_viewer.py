"""Measure the streamed multi-resolution viewer on the last dense job (docs/ARCHITECTURE.md).

    python -m pytest tests/e2e/test_dense.py -m "e2e and gpu"   # makes the job, saves its ids
    python benchmarks/measure_viewer.py

Software rendering (headless Chromium + SwiftShader, no GPU), against the local object store, so
the numbers show decode and draw cost, not network speed or a real GPU. Writes
benchmarks/results/gate5/viewer_lod.json.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

HOME = Path(os.environ.get("PARALLAXOPS_HOME") or Path(os.environ["LOCALAPPDATA"]) / "ParallaxOps")
WEB = os.environ.get("WEB_URL", "http://127.0.0.1:3000")
OUT = Path(__file__).parent / "results" / "gate5" / "viewer_lod.json"


def attrs(page: Page) -> dict[str, str]:
    return page.evaluate("() => ({...document.querySelector('[data-levels]').dataset})")


def orbit(page: Page, steps: int = 24) -> tuple[float, int]:
    """(mean wall ms of one drag step, points drawn while dragging). Each step renders once."""
    box = page.locator("canvas").bounding_box()
    assert box
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    t0 = time.perf_counter()
    for i in range(steps):
        page.mouse.move(x + 6 * (i + 1), y + 2 * (i + 1))
    elapsed = time.perf_counter() - t0
    drawn_while_moving = int(attrs(page)["drawn"])
    page.mouse.up()
    return round(1000 * elapsed / steps, 1), drawn_while_moving


def main() -> None:
    job = json.loads((HOME / "scratch" / "last_dense_job.json").read_text(encoding="utf-8"))
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(HOME / "tools" / "playwright"))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            args=[
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--ignore-gpu-blocklist",
            ]
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        ctx.add_init_script(f"window.localStorage.setItem('parallaxops.tenant', '{job['tenant']}')")
        page = ctx.new_page()
        page.goto(f"{WEB}/jobs/{job['job']}")
        expect(page.locator("main h1 span[aria-live]")).to_contain_text("COMPLETED", timeout=30_000)
        expect(page.locator("[data-done-ms]")).to_be_visible(timeout=60_000)
        sparse = attrs(page)

        page.get_by_role("radio", name=re.compile("Dense MVS")).check()
        # the sparse cloud's attributes are cleared on switch; wait for the dense cloud to finish
        expect(page.locator("[data-done-ms]")).to_have_attribute(
            "data-total", re.compile(r"^\d{5,}$"), timeout=120_000
        )
        dense = attrs(page)
        levels = [int(v) for v in dense["levels"].split(",")]
        page.wait_for_timeout(1500)

        at_rest = int(attrs(page)["drawn"])
        full_step, moving = orbit(page)
        page.wait_for_timeout(600)  # the viewer settles 180 ms after the last camera change
        settled = int(attrs(page)["drawn"])

        # cost of drawing each level while still: pick it in the Detail control, then orbit
        page.locator("label:has-text('Detail') select").select_option(index=2)
        mid_label = page.locator("label:has-text('Detail') select option:checked").inner_text()
        mid_drawn = int(attrs(page)["drawn"])
        mid_step, _ = orbit(page)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps(
                {
                    "job_points_total": int(dense["total"]),
                    "levels_cumulative": levels,
                    "sparse_cloud": {k: sparse[k] for k in ("total", "firstMs", "doneMs")},
                    "dense_first_level_ms": int(dense["firstMs"]),
                    "dense_all_points_ms": int(dense["doneMs"]),
                    "drawn_at_rest": at_rest,
                    "drawn_while_orbiting": moving,
                    "drawn_after_settling": settled,
                    "orbit_step_ms_detail_full": full_step,
                    "orbit_step_ms_detail_level3": mid_step,
                    "detail_level3_label": mid_label,
                    "detail_level3_points": mid_drawn,
                    "renderer": "headless Chromium, SwiftShader software GL, local object store",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        page.screenshot(path=str(HOME / "scratch" / "viewer_dense.png"))
        browser.close()
    print(OUT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
