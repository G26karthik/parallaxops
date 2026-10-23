"""Aggregate runs.jsonl into summary tables, hypothesis verdicts and charts. Measured data only.

    python benchmarks/report.py

Writes benchmarks/results/gate4/{summary.json,summary.csv} and docs/assets/gate4/*.png. The
verdict logic mirrors docs/EXPERIMENT_PLAN.md exactly; thresholds are not tuned after seeing data.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RESULTS = Path(__file__).parent / "results" / "gate4"
ASSETS = Path(__file__).resolve().parents[1] / "docs" / "assets" / "gate4"
METRICS = [
    "registered_fraction",
    "n_points3d",
    "reproj_mean_px",
    "gt_ate_over_spread",
    "gt_rot_median_deg",
    "wall_s",
    "cpu_s",
]
VARIANTS = ["clean", "blur", "overlap", "repeats", "light", "moving"]


def load() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (RESULTS / "runs.jsonl").read_text().splitlines()
        if line.strip()
    ]


def stats(rows: list[dict[str, Any]], key: str) -> dict[str, float] | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return {"mean": mean(vals), "min": min(vals), "max": max(vals), "n": len(vals)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    g: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        g[(r["scene"], r["variant"], r["arm"])].append(r)
    out: dict[str, Any] = {}
    for (scene, variant, arm), rs in g.items():
        out.setdefault(scene, {}).setdefault(variant, {})[arm] = {
            "n_runs": len(rs),
            "n_failed": sum(1 for r in rs if r["status"] != "completed"),
            "attempts_mean": mean(r.get("attempts", 1) for r in rs),
            "strategies": sorted({s for r in rs for s in r.get("strategies", [])}),
            **{m: stats(rs, m) for m in METRICS},
        }
    return out


def verdicts(summary: dict[str, Any], scene: str) -> dict[str, Any]:
    s = summary.get(scene, {})
    v: dict[str, Any] = {}
    # H1: helps where the baseline is below 90% registered
    weak = [
        x
        for x in VARIANTS
        if x in s
        and s[x].get("baseline", {}).get("registered_fraction")
        and s[x]["baseline"]["registered_fraction"]["mean"] < 0.9
        and "controller" in s[x]
    ]
    if not weak:
        v["H1"] = {
            "verdict": "not testable",
            "why": "the baseline registered at least 90% of images on every variant",
        }
    else:
        detail = {}
        for x in weak:
            b, c = s[x]["baseline"], s[x]["controller"]
            gain = c["registered_fraction"]["mean"] - b["registered_fraction"]["mean"]
            outside = c["registered_fraction"]["min"] > b["registered_fraction"]["max"]
            cost = c["wall_s"]["mean"] / b["wall_s"]["mean"]
            detail[x] = {
                "gain_pp": round(100 * gain, 1),
                "outside_baseline_range": outside,
                "cost_x": round(cost, 2),
                "supported": gain >= 0.10 and outside and cost <= 2.5,
            }
        ok = [d["supported"] for d in detail.values()]
        v["H1"] = {
            "verdict": "supported"
            if all(ok)
            else ("not supported" if not any(ok) else "mixed"),
            "variants": detail,
        }
    # H2: no waste on clean data
    if "clean" in s and "controller" in s["clean"]:
        b, c = s["clean"]["baseline"], s["clean"]["controller"]
        overhead = c["wall_s"]["mean"] / b["wall_s"]["mean"] - 1
        v["H2"] = {
            "verdict": "supported"
            if c["attempts_mean"] == 1 and overhead <= 0.05
            else "not supported",
            "attempts_mean": c["attempts_mean"],
            "overhead_pct": round(100 * overhead, 1),
        }
    # H3: no accuracy regression where both register >= 90%
    d3 = {}
    for x in VARIANTS:
        if x in s and "controller" in s[x]:
            b, c = s[x]["baseline"], s[x]["controller"]
            if (
                b["registered_fraction"]
                and c["registered_fraction"]
                and min(
                    b["registered_fraction"]["mean"], c["registered_fraction"]["mean"]
                )
                >= 0.9
                and b["gt_ate_over_spread"]
                and c["gt_ate_over_spread"]
            ):
                d3[x] = {
                    "controller_mean": c["gt_ate_over_spread"]["mean"],
                    "baseline_max": b["gt_ate_over_spread"]["max"],
                    "ok": c["gt_ate_over_spread"]["mean"]
                    <= b["gt_ate_over_spread"]["max"],
                }
    if d3:
        v["H3"] = {
            "verdict": "supported"
            if all(d["ok"] for d in d3.values())
            else "not supported",
            "variants": d3,
        }
    return v


def fmt(st: dict[str, float] | None, digits: int = 3) -> str:
    if not st:
        return "-"
    return f"{st['mean']:.{digits}f} [{st['min']:.{digits}f}-{st['max']:.{digits}f}]"


def charts(summary: dict[str, Any], scene: str) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    s = summary[scene]
    vs = [x for x in VARIANTS if x in s]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), dpi=140)
    spec = [
        ("registered_fraction", "Registered image fraction (higher is better)", False),
        (
            "gt_ate_over_spread",
            "Camera position error / rig spread (lower is better)",
            True,
        ),
        ("wall_s", "Wall clock, all attempts (s)", False),
    ]
    for ax, (key, title, log) in zip(axes, spec, strict=True):
        for k, arm in enumerate(("baseline", "controller")):
            xs, ys, lo, hi = [], [], [], []
            for i, x in enumerate(vs):
                st = s[x].get(arm, {}).get(key)
                if st:
                    xs.append(i + (k - 0.5) * 0.38)
                    ys.append(st["mean"])
                    lo.append(st["mean"] - st["min"])
                    hi.append(st["max"] - st["mean"])
            ax.bar(
                xs,
                ys,
                width=0.36,
                yerr=[lo, hi],
                capsize=3,
                label=arm,
                color="#8a9bb0" if arm == "baseline" else "#e0a030",
            )
        ax.set_xticks(range(len(vs)), vs, rotation=20)
        ax.set_title(title, fontsize=10)
        if log:
            ax.set_yscale("log")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, frameon=False)
    fig.suptitle(
        f"{scene}: baseline vs controller (bars = mean, whiskers = min-max over repetitions)",
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(ASSETS / f"{scene}_comparison.png")
    plt.close(fig)


def main() -> None:
    rows = load()
    summary = summarize(rows)
    out = {
        "summary": summary,
        "verdicts": {sc: verdicts(summary, sc) for sc in summary},
        "n_runs": len(rows),
        "n_errors": sum(1 for r in rows if r["status"] == "error"),
    }
    (RESULTS / "summary.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    with (RESULTS / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "scene",
                "variant",
                "arm",
                "runs",
                "failed",
                "attempts",
                "registered_fraction",
                "points",
                "reproj_px",
                "gt_ate_over_spread",
                "wall_s",
            ]
        )
        for sc, vs in summary.items():
            for variant, arms in vs.items():
                for arm, d in arms.items():
                    w.writerow(
                        [
                            sc,
                            variant,
                            arm,
                            d["n_runs"],
                            d["n_failed"],
                            round(d["attempts_mean"], 2),
                            fmt(d["registered_fraction"]),
                            fmt(d["n_points3d"], 0),
                            fmt(d["reproj_mean_px"]),
                            fmt(d["gt_ate_over_spread"], 4),
                            fmt(d["wall_s"], 1),
                        ]
                    )
    for sc in summary:
        charts(summary, sc)
    print(json.dumps(out["verdicts"], indent=2))


if __name__ == "__main__":
    main()
