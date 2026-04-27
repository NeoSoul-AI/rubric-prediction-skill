"""Regression tests for rubric_forecast.engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rubric_forecast.engine import run_forecast

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_INPUT = REPO_ROOT / "examples" / "polymarket_hormuz_geopolitics_input.json"


def test_example_input_ok() -> None:
    payload = json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))
    result = run_forecast(payload)
    assert result["status"] == "ok"
    options = list(payload["options"])
    assert result["final_answer"] in options
    assert "normalized_scores" in result
    assert len(result["normalized_scores"]) == len(options)
    assert "evidence_ledger" in result
    assert "sensitivity" in result
    eng = result["engine"]
    assert eng["name"] == "rubric-forecast-engine"
    assert eng["strict_decoupling"] is True
    assert eng["computed_by"] == "rubric_forecast.engine"
    assert eng.get("input_sha256")


def test_insufficient_spec_missing_fields() -> None:
    result = run_forecast({})
    assert result["status"] == "insufficient_spec"
    assert "missing_fields" in result
    assert result["engine"]["computed_by"] == "rubric_forecast.engine"


@pytest.mark.parametrize(
    "bad",
    [None, [], "not-an-object"],
)
def test_insufficient_spec_non_object(bad: object) -> None:
    result = run_forecast(bad)
    assert result["status"] == "insufficient_spec"
