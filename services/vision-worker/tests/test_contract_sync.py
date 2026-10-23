"""Python and TypeScript keep hand-copied lists of the same things. These tests fail when they drift.

A diagnostic code that exists only in Python makes `GET /v1/jobs/{id}` return 500 for every job that
holds it (the API validates responses against the TypeScript list); a config key missing from the
worker's mapping is silently dropped.
"""

from __future__ import annotations

import re
from pathlib import Path

from parallaxops_vision.config import ReconstructionConfig
from parallaxops_vision.diagnostics import DiagnosticCode
from parallaxops_vision.engine import choose_matching
from parallaxops_vision.worker import activities

API_TS = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "src" / "api.ts"


def ts_block(name: str) -> str:
    m = re.search(
        rf"export const {name} = (?:\[|z\.object\(\{{)(.*?)(?:\] as const|\}}\);)",
        API_TS.read_text(),
        re.S,
    )
    assert m, f"{name} not found in {API_TS.name}"
    return m.group(1)


def test_diagnostic_codes_match_between_python_and_typescript() -> None:
    ts = set(re.findall(r'"([A-Z_]+)"', ts_block("DIAGNOSTIC_CODES")))
    assert ts == {c.value for c in DiagnosticCode}


def test_every_api_config_key_reaches_the_worker_config() -> None:
    ts = set(re.findall(r"^\s{2}(\w+): z", ts_block("ReconstructionConfigSchema"), re.M))
    assert ts == set(activities._CAMEL), "an API config key would be silently ignored by the worker"
    # and each mapped key really exists on the Python config model
    assert set(activities._CAMEL.values()) <= set(ReconstructionConfig.model_fields)


def test_matching_policy_switches_to_sequential_only_above_sixty_images() -> None:
    cfg = ReconstructionConfig()
    assert choose_matching(23, cfg)[0] == "exhaustive"
    assert choose_matching(60, cfg)[0] == "exhaustive"
    policy, why = choose_matching(61, cfg)
    assert policy == "sequential" and "capture order" in why  # the assumption is stated, not hidden
    explicit = ReconstructionConfig(matching="exhaustive")
    assert choose_matching(80, explicit)[0] == "exhaustive"  # an explicit choice always wins
