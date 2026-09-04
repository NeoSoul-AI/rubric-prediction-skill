"""Tests for policy engine."""

from __future__ import annotations

import json
from pathlib import Path

from rubric_forecast.policy import PolicyEngine


def test_policy_allows_when_empty_whitelist_means_skip(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    state_path = tmp_path / "state.json"
    policy_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "chain_whitelist": [56],
                "contract_whitelist": [],
                "function_whitelist": [],
                "limits": {
                    "single_tx_max_gas": 500000,
                    "daily_gas_budget_units": 10**12,
                    "daily_action_limit": 100,
                },
            }
        ),
        encoding="utf-8",
    )
    eng = PolicyEngine(policy_path, state_path)
    r = eng.check_action(chain_id=56, action_type="generic", estimated_gas=100000)
    assert r.allowed


def test_chain_blocked(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    state_path = tmp_path / "state.json"
    policy_path.write_text(
        json.dumps({"enabled": True, "chain_whitelist": [1]}),
        encoding="utf-8",
    )
    eng = PolicyEngine(policy_path, state_path)
    r = eng.check_action(chain_id=56, action_type="generic")
    assert not r.allowed
    assert any("whitelist" in x for x in r.reasons)
