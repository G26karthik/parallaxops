from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parallaxops_vision import controller
from parallaxops_vision.config import ReconstructionConfig


def ok(
    frac: float = 1.0,
    err: float = 0.8,
    tri: float = 10.0,
    per_image: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": "completed",
        "metrics": {
            "registered_fraction": frac,
            "reproj_error_px_mean": err,
            "triangulation_angle_deg_median": tri,
            "n_points3d": 1000,
            "per_image_error": per_image or [],
        },
    }


def failed(code: str) -> dict[str, Any]:
    return {"status": "failed", "diagnostic": {"code": code}}


@pytest.mark.parametrize(
    ("report", "family"),
    [
        (ok(1.0, 0.8), "accept"),
        (ok(0.7, 0.8), "registration"),
        (ok(1.0, 2.5), "reprojection"),
        (failed("LOW_REGISTRATION"), "registration"),
        (failed("INSUFFICIENT_MATCHES"), "registration"),
        (failed("TIMEOUT"), "stop"),
        (failed("CANCELLED"), "stop"),
    ],
)
def test_diagnosis_uses_measured_metrics_only(report: dict[str, Any], family: str) -> None:
    assert controller.diagnose(report)[0] == family


def test_registration_family_walks_the_menu_without_repeating() -> None:
    cfg = ReconstructionConfig()
    r = ok(0.6)
    s1 = controller.next_strategy("registration", r, cfg, set(), set())
    assert s1 and s1.name == "more_features_and_scale"
    assert s1.config["max_num_features"] == 16384 and s1.config["max_image_size"] == 2000
    s2 = controller.next_strategy("registration", r, cfg, {s1.name}, set())
    assert s2 and s2.name == "global_mapper" and s2.config == {"mapper": "global"}
    assert controller.next_strategy("registration", r, cfg, {s1.name, s2.name}, set()) is None


def test_feature_escalation_is_capped() -> None:
    cfg = ReconstructionConfig(max_num_features=16384, max_image_size=3000)
    s = controller.next_strategy("registration", ok(0.5), cfg, set(), set())
    assert s and s.config["max_num_features"] == 16384 and s.config["max_image_size"] == 3200


def test_exclusion_targets_worst_residuals_and_is_capped_at_20_percent() -> None:
    rows = [{"name": f"{i}.jpg", "n_obs": 100, "mean_error_px": 0.8} for i in range(20)]
    for i in range(6):
        rows[i]["mean_error_px"] = 5.0 + i  # six clear outliers; only 20% (4) may be dropped
    s = controller.next_strategy(
        "reprojection",
        ok(1.0, 2.0, per_image=rows),
        ReconstructionConfig(),
        {"opencv_camera"},
        set(),
    )
    assert s and s.name == "exclude_high_residual"
    assert len(s.exclude) == 4 and s.exclude[0] == "5.jpg"  # worst first


def test_score_prefers_registration_then_error() -> None:
    assert controller.score(ok(1.0, 2.0)) > controller.score(ok(0.9, 0.5))
    assert controller.score(ok(1.0, 0.8)) > controller.score(ok(1.0, 1.2))
    assert controller.score(failed("NO_FEATURES")) < controller.score(ok(0.1, 9.0))


class Harness:
    """Replaces the engine with scripted reports so policy logic is tested without COLMAP."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        script: list[dict[str, Any]],
        blurry: int = 0,
        n_images: int = 20,
    ):
        self.configs: list[ReconstructionConfig] = []
        self.script = script
        images = [
            SimpleNamespace(name=f"{i}.jpg", flags=["possibly_blurry"] if i < blurry else [])
            for i in range(n_images)
        ]
        monkeypatch.setattr(controller, "run_qc", lambda *_a, **_k: SimpleNamespace(images=images))

        def fake(_inp: Path, out: Path, cfg: ReconstructionConfig, **_kw: Any) -> dict[str, Any]:
            self.configs.append(cfg)
            out.mkdir(parents=True)
            (out / "report.json").write_text("{}")
            return script[len(self.configs) - 1]

        monkeypatch.setattr(controller, "reconstruct", fake)


def test_accepts_first_attempt_when_good(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, [ok(1.0, 0.8)])
    res = controller.run_controlled(tmp_path / "in", tmp_path / "out")
    assert res["attempts"] == 1 and res["log"][0]["decision"] == "stop: accepted"
    assert (tmp_path / "out" / "controller_log.json").exists()
    assert len(h.configs) == 1 and h.configs[0].mapper == "incremental"  # untouched baseline


def test_escalates_then_keeps_the_best_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch, [ok(0.6), ok(0.95, 0.9), ok(0.5)])
    res = controller.run_controlled(tmp_path / "in", tmp_path / "out", baseline_budget_s=1e6)
    # attempt 2 reaches 95% and is accepted, so there is no third attempt
    assert res["attempts"] == 2 and res["best_attempt"] == 2
    assert h.configs[1].max_num_features == 16384
    assert "more_features_and_scale" in res["log"][0]["decision"]


def test_attempt_limit_and_best_selection_when_nothing_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Harness(monkeypatch, [ok(0.6), ok(0.7), ok(0.65)])
    res = controller.run_controlled(tmp_path / "in", tmp_path / "out", baseline_budget_s=1e6)
    assert res["attempts"] == 3 and res["best_attempt"] == 2  # 0.7 beats 0.6 and 0.65
    assert res["advice"] and "more photographs" in res["advice"]
    # both menu entries were used by attempt 3, so it stops for lack of strategies (or the limit)
    assert res["log"][-1]["decision"].startswith("stop:")
    assert res["log"][-1]["attempt"] == 3


def test_budget_blocks_strategies_that_cannot_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Harness(monkeypatch, [ok(0.6), ok(0.99)])
    res = controller.run_controlled(tmp_path / "in", tmp_path / "out", baseline_budget_s=0.0)
    assert res["attempts"] == 1
    assert "remaining budget" in res["log"][0]["decision"]


def test_pre_run_blur_exclusion_only_when_affordable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch, [ok(1.0)], blurry=4, n_images=20)  # 20% blurry: allowed
    res = controller.run_controlled(tmp_path / "in", tmp_path / "out")
    assert res["log"][0]["action"] == "exclude_blurry" and len(h.configs[0].exclude_images) == 4

    h2 = Harness(monkeypatch, [ok(1.0)], blurry=10, n_images=20)  # 50% blurry: too many to drop
    controller.run_controlled(tmp_path / "in", tmp_path / "out2")
    assert h2.configs[0].exclude_images == []


def test_never_reads_ground_truth_for_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    poisoned = ok(0.6)
    poisoned["ground_truth_eval"] = None  # any attempt to index into it would raise
    Harness(monkeypatch, [poisoned, ok(1.0)])
    res = controller.run_controlled(tmp_path / "in", tmp_path / "out", baseline_budget_s=1e6)
    assert res["attempts"] == 2
