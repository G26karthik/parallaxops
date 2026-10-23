from __future__ import annotations

import json
from pathlib import Path

import pytest

from parallaxops_vision.diagnostics import DiagnosticCode, ReconstructionError
from parallaxops_vision.pipeline import read_engine_result
from parallaxops_vision.supervisor import SupervisedResult


def res(code: int | None = 0, killed: str | None = None, log: str = "") -> SupervisedResult:
    return SupervisedResult(
        returncode=code,
        killed_reason=killed,
        wall_seconds=1,
        peak_rss_bytes=1,
        cpu_seconds=1,
        log_tail=log,
    )


@pytest.mark.parametrize(
    ("killed", "code"),
    [
        ("timeout", DiagnosticCode.TIMEOUT),
        ("cancelled", DiagnosticCode.CANCELLED),
        ("memory", DiagnosticCode.MEMORY_BUDGET_EXCEEDED),
        ("disk", DiagnosticCode.DISK_BUDGET_EXCEEDED),
    ],
)
def test_budget_kills_become_typed_diagnostics(
    tmp_path: Path, killed: str, code: DiagnosticCode
) -> None:
    with pytest.raises(ReconstructionError) as e:
        read_engine_result(tmp_path, res(code=None, killed=killed))
    assert e.value.diagnostic.code is code


def test_crash_without_a_result_file_keeps_the_traceback(tmp_path: Path) -> None:
    (tmp_path / "engine_traceback.txt").write_text("Traceback...\nBoom")
    with pytest.raises(ReconstructionError) as e:
        read_engine_result(tmp_path, res(code=3))
    d = e.value.diagnostic
    assert d.code is DiagnosticCode.ENGINE_CRASH and "Boom" in str(d.details["traceback_tail"])


def test_crash_falls_back_to_the_log_tail_when_there_is_no_traceback(tmp_path: Path) -> None:
    with pytest.raises(ReconstructionError) as e:
        read_engine_result(tmp_path, res(code=1, log="segfault in matcher"))
    assert "segfault" in str(e.value.diagnostic.details["traceback_tail"])


def test_zero_exit_but_missing_result_is_still_a_crash(tmp_path: Path) -> None:
    with pytest.raises(ReconstructionError) as e:
        read_engine_result(tmp_path, res(code=0))
    assert e.value.diagnostic.code is DiagnosticCode.ENGINE_CRASH


def test_engine_reported_diagnostic_is_passed_through(tmp_path: Path) -> None:
    diag = {
        "code": "NO_FEATURES",
        "message": "too dark",
        "advice": "light",
        "stage": "feature_extraction",
        "details": {},
    }
    (tmp_path / "engine_result.json").write_text(json.dumps({"ok": False, "diagnostic": diag}))
    with pytest.raises(ReconstructionError) as e:
        read_engine_result(tmp_path, res())
    assert (
        e.value.diagnostic.code is DiagnosticCode.NO_FEATURES
        and e.value.diagnostic.stage == "feature_extraction"
    )


def test_success_returns_the_engine_payload(tmp_path: Path) -> None:
    (tmp_path / "engine_result.json").write_text(
        json.dumps({"ok": True, "invocation": {}, "best_model_dir": "sparse/0"})
    )
    assert read_engine_result(tmp_path, res())["best_model_dir"] == "sparse/0"
