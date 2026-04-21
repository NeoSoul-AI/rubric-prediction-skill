"""Dry-run AutopilotDaemon cycle with a local candidates file (no RPC / keystore)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CANDIDATES = _REPO_ROOT / "examples" / "autopilot_candidates.json"


@pytest.fixture()
def autopilot_env(monkeypatch, tmp_path):
    for key in (
        "PRIVATE_KEY",
        "AUTOPILOT_PRIVATE_KEY",
        "ETH_PRIVATE_KEY",
        "EXECUTOR_PRIVATE_KEY",
        "OPENCLAW_BASE_URL",
        "OPENCLAW_API_KEY",
        "AUTOPILOT_OPENCLAW_KEY",
        "LIFEFUN_API_BASE_URL",
        "AUTOPILOT_RPC_URL",
        "RPC_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("AUTOPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_CANDIDATES_FILE", str(_CANDIDATES))
    monkeypatch.setenv("AUTOPILOT_DRY_RUN", "true")
    # Demo candidate may score below the default 0.55 bar; keep threshold low for this smoke test.
    monkeypatch.setenv("AUTOPILOT_MIN_SCORE", "0.01")


def test_daemon_run_once_dry_run(autopilot_env):
    from rubric_forecast.config import AutopilotConfig
    from rubric_forecast.daemon import AutopilotDaemon, ensure_policy_file
    from rubric_forecast.policy import DEFAULT_POLICY

    cfg = AutopilotConfig.from_env()
    ensure_policy_file(cfg.policy_path)
    # Daemon calls check_action with implicit function_name "generic"; empty whitelist skips the fn check (see tests/test_policy.py).
    smoke_policy = {**DEFAULT_POLICY, "function_whitelist": []}
    cfg.policy_path.write_text(json.dumps(smoke_policy, indent=2), encoding="utf-8")

    daemon = AutopilotDaemon(cfg)
    summary = daemon.run_once(keystore_password="")

    assert summary.get("candidates", 0) >= 1
    assert summary.get("actions_planned", 0) >= 1
    # Dry-run path records execute events without RPC when policy allows and score >= min
    assert summary.get("executed", 0) >= 1
