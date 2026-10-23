"""POST-HOC analysis (not pre-registered): why did the controller's pose error rise on blur/overlap?

    python benchmarks/posthoc.py

Reads runs_posthoc.jsonl (re-runs that also store per-image position errors). For each variant and
repetition it compares the baseline's and the controller's registered images:
  * on images BOTH arms registered, how large is each arm's position error (relative to the GT rig
    spread)? This removes the confound that the arms register different image sets;
  * how many images only the controller registered, and how wrong are they?
Findings from this file are exploratory and are labelled as such in docs/EXPERIMENTS.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

RESULTS = Path(__file__).parent / "results" / "gate4"
BAD = 0.05  # a camera more than 5% of the rig spread from ground truth counts as badly placed


def main() -> None:
    rows = [
        json.loads(line)
        for line in (RESULTS / "runs_posthoc.jsonl").read_text().splitlines()
        if line.strip()
    ]
    by: dict[tuple[str, int], dict[str, dict]] = {}
    for r in rows:
        if r["status"] == "completed" and r.get("gt_per_image_pos_err") is not None:
            by.setdefault((r["variant"], r["rep"]), {})[r["arm"]] = r
    out = []
    for (variant, rep), arms in sorted(by.items()):
        if {"baseline", "controller"} <= arms.keys():
            b, c = arms["baseline"], arms["controller"]
            spread = b["gt_spread"] or 1.0
            eb, ec = b["gt_per_image_pos_err"], c["gt_per_image_pos_err"]
            common = sorted(set(eb) & set(ec))
            only_c = sorted(set(ec) - set(eb))
            out.append(
                {
                    "variant": variant,
                    "rep": rep,
                    "baseline_registered": len(eb),
                    "controller_registered": len(ec),
                    "common": len(common),
                    "common_err_over_spread_baseline": round(
                        mean(eb[k] for k in common) / spread, 4
                    )
                    if common
                    else None,
                    "common_err_over_spread_controller": round(
                        mean(ec[k] for k in common) / spread, 4
                    )
                    if common
                    else None,
                    "controller_only": len(only_c),
                    "controller_only_bad": sum(
                        1 for k in only_c if ec[k] / spread > BAD
                    ),
                    "controller_bad_total": sum(
                        1 for v in ec.values() if v / spread > BAD
                    ),
                    "baseline_bad_total": sum(
                        1 for v in eb.values() if v / spread > BAD
                    ),
                }
            )
    (RESULTS / "posthoc_summary.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    for o in out:
        print(o)


if __name__ == "__main__":
    main()
