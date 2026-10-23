"""`vision` command line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .config import ReconstructionConfig
from .diagnostics import EXIT_CODES, DiagnosticCode, ReconstructionError

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ParallaxOps vision worker CLI.")
console = Console(stderr=True)


@app.command()
def env() -> None:
    """Print tool versions and host capabilities (no personal paths)."""
    from .envinfo import host_info, tool_versions

    print(json.dumps({"tools": tool_versions(), "host": host_info()}, indent=2))


@app.command()
def qc(
    input: Annotated[Path, typer.Option("--input", "-i", help="Directory of images.")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write QC JSON here.")
    ] = None,
) -> None:
    """Run image QC only."""
    from .qc import run_qc

    try:
        rep = run_qc(input, ReconstructionConfig())
    except ReconstructionError as exc:
        console.print(
            f"[red]{exc.diagnostic.code}[/red]: {exc.diagnostic.message}\n{exc.diagnostic.advice}"
        )
        raise typer.Exit(EXIT_CODES[exc.diagnostic.code]) from exc
    text = rep.model_dump_json(indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        print(text)


@app.command()
def reconstruct(
    input: Annotated[
        Path, typer.Option("--input", "-i", help="Directory of overlapping JPG/PNG images.")
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="Artifact directory to create.")],
    config: Annotated[
        Path | None, typer.Option(help="JSON file with ReconstructionConfig overrides.")
    ] = None,
    seed: Annotated[int, typer.Option(help="Random seed for COLMAP RANSAC.")] = 0,
    max_image_size: Annotated[
        int | None, typer.Option(help="Longest image side used by SfM.")
    ] = None,
    mapper: Annotated[str | None, typer.Option(help="incremental | global")] = None,
    camera_model: Annotated[
        str | None, typer.Option(help="SIMPLE_RADIAL | RADIAL | OPENCV | ...")
    ] = None,
    ground_truth: Annotated[
        Path | None, typer.Option(help="COLMAP images.txt with GT poses (ETH3D) for accuracy eval.")
    ] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing output directory.")] = False,
    keep_database: Annotated[bool, typer.Option(help="Also publish COLMAP database.db.")] = False,
    no_render: Annotated[bool, typer.Option(help="Skip PNG renders.")] = False,
) -> None:
    """Reconstruct a scene from images. Exit 0 on success; a typed non-zero code on failure."""
    from .pipeline import reconstruct as run

    raw = json.loads(config.read_text(encoding="utf-8")) if config else {}
    raw["seed"] = seed
    if max_image_size:
        raw["max_image_size"] = max_image_size
    if mapper:
        raw["mapper"] = mapper
    if camera_model:
        raw["camera_model"] = camera_model
    cfg = ReconstructionConfig.model_validate(raw)

    def on_event(e: dict[str, Any]) -> None:
        if e.get("state") != "heartbeat":
            console.print(f"[dim]{e['elapsed']:7.1f}s[/dim] {e['stage']} {e['state']}")

    report = run(
        input,
        output,
        cfg,
        on_event=on_event,
        gt_images_txt=ground_truth,
        force=force,
        keep_database=keep_database,
        render=not no_render,
    )
    if report["status"] != "completed":
        d = report["diagnostic"]
        console.print(f"[bold red]FAILED[/bold red] {d['code']}: {d['message']}")
        console.print(f"Advice: {d['advice']}")
        console.print(f"Trace preserved in {output}")
        raise typer.Exit(EXIT_CODES.get(DiagnosticCode(d["code"]), 1))

    m = report["metrics"]
    t = Table(title=f"ParallaxOps reconstruction ({m['quality']['level']})")
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_row("images registered", f"{m['n_registered']}/{m['n_input_images']}")
    t.add_row("sparse 3D points", str(m["n_points3d"]))
    t.add_row("mean track length", f"{m['track_length_mean']:.2f}")
    t.add_row(
        "reprojection error mean / median (px)",
        f"{m['reproj_error_px_mean']:.3f} / {m['reproj_error_px_median']:.3f}",
    )
    t.add_row("median triangulation angle (deg)", f"{m['triangulation_angle_deg_median']:.2f}")
    t.add_row("wall clock (s)", f"{report['timings_seconds']['total']:.1f}")
    if "ground_truth_eval" in report:
        g = report["ground_truth_eval"]
        t.add_row("GT: camera position RMSE (GT units)", f"{g['ate_rmse_all']:.4f}")
        t.add_row("GT: rotation error median (deg)", f"{g['rotation_error_deg_median']:.2f}")
    console.print(t)
    console.print(f"Artifacts: {output}")


if __name__ == "__main__":
    app()
