"""Browser end-to-end: the whole product path in a real (headless) Chromium against the live stack.

    scripts/dev.ps1 up
    python -m pytest tests/e2e/test_ui.py -m e2e -s

Drives the real UI: create project and dataset, upload real photos through presigned URLs, run
preflight, start a reconstruction, watch the timeline, inspect the 3D scene, re-run, compare, and
see a failed job's diagnostics. Screenshots of the working UI are written to docs/assets/ui/.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

pytest.importorskip("playwright")
from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

WEB = os.environ.get("WEB_URL", "http://127.0.0.1:3000")
API = os.environ.get("API_URL", "http://127.0.0.1:4000")
HOME = Path(
    os.environ.get("PARALLAXOPS_HOME")
    or Path(os.environ.get("LOCALAPPDATA", "")) / "ParallaxOps"
)
SRC = HOME / "data" / "eth3d" / "terrace" / "terrace" / "images" / "dslr_images"
SHOTS = Path(__file__).resolve().parents[2] / "docs" / "assets" / "ui"
AXE = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "web"
    / "node_modules"
    / "axe-core"
    / "axe.min.js"
)


def stack_up() -> bool:
    try:
        return (
            httpx.get(f"{API}/readyz", timeout=3).status_code == 200
            and httpx.get(WEB, timeout=5).status_code == 200
        )
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not stack_up(), reason="local stack (incl. web) is not running"),
]


@pytest.fixture(scope="module")
def photos(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    if not SRC.is_dir():
        pytest.skip("ETH3D terrace not downloaded")
    out = tmp_path_factory.mktemp("ui_photos")
    files = []
    for p in sorted(SRC.glob("*.JPG")):
        im = Image.open(p)
        exif = im.getexif()
        im.thumbnail((2000, 2000))
        dst = out / p.name
        im.save(dst, "JPEG", quality=90, exif=exif.tobytes())
        files.append(dst)
    return files


@pytest.fixture(scope="module")
def noise(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    out = tmp_path_factory.mktemp("noise")
    files = []
    for i in range(6):
        rng = np.random.default_rng(100 + i)
        p = out / f"noise_{i}.jpg"
        Image.fromarray(rng.integers(0, 256, (360, 480, 3), dtype=np.uint8)).save(
            p, quality=95
        )
        files.append(p)
    return files


@pytest.fixture(scope="module")
def page():  # type: ignore[no-untyped-def]
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(HOME / "tools" / "playwright")
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            args=[
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--ignore-gpu-blocklist",
            ]
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900}, color_scheme="dark"
        )
        pg = ctx.new_page()
        pg.set_default_timeout(30_000)
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on(
            "console",
            lambda m: (
                errors.append(m.text)
                if m.type == "error" and "favicon" not in m.text
                else None
            ),
        )
        pg.errors = errors  # type: ignore[attr-defined]
        yield pg
        browser.close()


def shot(page: Page, name: str, **kw) -> None:  # type: ignore[no-untyped-def]
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), **kw)


def a11y(page: Page, label: str) -> None:
    """Run axe-core; fail on serious/critical violations."""
    if not AXE.exists():
        pytest.skip("axe-core not installed")
    page.add_script_tag(path=str(AXE))
    res = page.evaluate(
        "async () => (await axe.run(document, {runOnly:['wcag2a','wcag2aa']})).violations"
        ".map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help}))"
    )
    bad = [v for v in res if v["impact"] in {"serious", "critical"}]
    assert not bad, f"{label}: accessibility violations {bad}"


def upload(page: Page, files: list[Path], expect_status: str = "Verified") -> None:
    page.locator("input[type=file]").set_input_files([str(f) for f in files])
    expect(
        page.locator("ul[aria-live=polite] li").filter(has_text=expect_status)
    ).to_have_count(len(files), timeout=240_000)


def wait_state(page: Page, state: str, timeout: int = 420_000) -> None:
    expect(page.locator("main h1 span[aria-live]")).to_contain_text(
        state, timeout=timeout
    )


def create_project_and_dataset(page: Page, project: str, dataset: str) -> None:
    page.get_by_placeholder("New project name").fill(project)
    page.get_by_role("button", name="Create project").click()
    page.get_by_role("link", name=re.compile(project)).click()
    page.get_by_placeholder("e.g. Terrace, morning").fill(dataset)
    page.get_by_role("button", name="Add dataset").click()
    page.get_by_role("link", name=re.compile(dataset)).click()


def test_keyboard_and_landing(page: Page) -> None:
    page.goto(WEB)
    expect(page.get_by_role("heading", level=1)).to_contain_text(
        "Photos in. Geometry out."
    )
    expect(
        page.get_by_role("status").filter(has_text="All systems ready")
    ).to_be_visible(timeout=20_000)
    expect(page.get_by_text("No projects yet")).to_be_visible()
    shot(page, "01-home-empty")
    page.keyboard.press("Tab")
    expect(page.locator(":focus")).to_have_text(
        "Skip to content"
    )  # keyboard users can skip the header
    a11y(page, "home")


def test_full_user_journey(page: Page, photos: list[Path], noise: list[Path]) -> None:
    create_project_and_dataset(page, "Terrace demo", "Terrace, 23 photos")

    upload(page, photos)
    shot(page, "02-upload-verified")
    # server-verified, checksummed, deduplicated: re-uploading the same files changes nothing
    upload(page, photos[:3], expect_status="Duplicate")

    page.get_by_role("button", name="Run capture checks").click()
    expect(page.get_by_role("table", name="Per-image QC heuristics")).to_be_visible(
        timeout=180_000
    )
    assert page.locator(
        "table:has(caption:text('Per-image QC heuristics')) tbody tr"
    ).count() == len(photos)
    shot(page, "03-preflight")
    a11y(page, "dataset")

    page.get_by_label("Run label (optional)").fill("baseline")
    page.get_by_role("button", name="Start reconstruction").click()
    page.wait_for_url(re.compile(r"/jobs/"))
    wait_state(page, "RECONSTRUCTING", 120_000)
    shot(page, "04-job-running")
    wait_state(page, "COMPLETED")
    expect(page.get_by_text("Measured metrics")).to_be_visible()
    canvas = page.locator("canvas")
    expect(canvas).to_be_visible(timeout=60_000)
    assert "points" in (canvas.get_attribute("aria-label") or "")
    page.wait_for_timeout(1500)
    # The 3D view must actually contain rendered points, not just an empty canvas.
    pix = np.asarray(
        Image.open(io.BytesIO(canvas.screenshot())).convert("L"), dtype=float
    )
    assert pix.std() > 8 and (pix > pix.min() + 40).mean() > 0.002, (
        "viewer canvas looks blank"
    )
    shot(page, "05-job-completed", full_page=True)
    canvas.screenshot(path=str(SHOTS / "06-viewer.png"))
    expect(page.get_by_role("link", name="Sparse point cloud (PLY)")).to_be_visible()
    a11y(page, "job")

    # Re-run, then compare the two runs
    first_job_url = page.url
    page.get_by_role("button", name="Re-run with same settings").click()
    page.wait_for_url(
        lambda u: "/jobs/" in u and u != first_job_url
    )  # a NEW job page, not the old one
    wait_state(page, "COMPLETED")
    page.get_by_role("link", name="Project", exact=True).click()
    page.get_by_role("link", name=re.compile("Compare 2 completed runs")).click()
    expect(
        page.get_by_role("table", name="Metric comparison between runs")
    ).to_be_visible()
    shot(page, "07-compare", full_page=True)

    # A hopeless capture fails with an actionable diagnostic
    page.go_back()
    page.get_by_placeholder("e.g. Terrace, morning").fill("Noise (should fail)")
    page.get_by_role("button", name="Add dataset").click()
    page.get_by_role("link", name=re.compile("Noise")).click()
    upload(page, noise)
    dataset_url = page.url
    page.get_by_role("button", name="Start reconstruction").click()
    page.wait_for_url(lambda u: "/jobs/" in u and u != dataset_url)
    wait_state(page, "FAILED", 300_000)
    expect(page.get_by_text("What to do:")).to_be_visible()
    shot(page, "08-job-failed-diagnostics", full_page=True)

    assert not [e for e in page.errors if "ResizeObserver" not in e], page.errors  # type: ignore[attr-defined]
