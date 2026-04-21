"""Daemon write-action dispatch tests for feed_reference and adopt."""

from __future__ import annotations

import json
from pathlib import Path

from rubric_forecast.config import AutopilotConfig
from rubric_forecast.daemon import AutopilotDaemon


def mk_candidate(action_type: str, meta: dict) -> dict:
    return {
        "meta": {"id": f"{action_type}-id", "action_type": action_type, **meta},
        "question": "Will demo resolve yes?",
        "options": ["yes", "no"],
        "resolution_rule": "manual",
        "evidence": [
            {
                "id": "e1",
                "claim": "supporting signal",
                "source": "demo",
                "supports_option": "yes",
                "stance": "for",
                "strength": "strong",
                "dimension_scores": {
                    "reliability": 0.8,
                    "mechanism_fit": 0.8,
                    "novelty": 0.8,
                    "timeliness": 0.8,
                },
            }
        ],
    }


def mk_config(tmp_path: Path, candidates_file: Path) -> AutopilotConfig:
    cfg = AutopilotConfig(
        data_dir=tmp_path,
        keystore_path=tmp_path / "k.json",
        policy_path=tmp_path / "policy.json",
        state_path=tmp_path / "state.json",
        audit_log_path=tmp_path / "audit.jsonl",
        pause_file_path=tmp_path / "PAUSED",
        candidates_file=candidates_file,
        poll_interval_seconds=30,
        dry_run=False,
        chain_id=56,
        rpc_url=None,
        openclaw_base_url="https://api.example.com",
        openclaw_api_key="oc",
        lifefun_jwt="jwt",
        lifefun_jwt_file_path=tmp_path / "lifefun.jwt",
        lifefun_login_address=None,
        lifefun_login_signature_source=None,
        bundler_url=None,
        execution_mode="eoa",
        min_normalized_score=0.01,
        max_actions_per_cycle=5,
    )
    cfg.policy_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "chain_whitelist": [56],
                "function_whitelist": [],
                "limits": {
                    "single_tx_max_gas": 500000,
                    "daily_gas_budget_units": 10**12,
                    "daily_action_limit": 100,
                    "daily_action_limit_by_type": {
                        "feed_reference": 100,
                        "adopt": 100,
                        "generic": 100,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg


def test_daemon_dispatch_feed_reference(tmp_path: Path) -> None:
    candidates_file = tmp_path / "candidates.json"
    candidates_file.write_text(
        json.dumps([mk_candidate("feed_reference", {"source_opinion_id": 1, "target_agent_id": 2})]),
        encoding="utf-8",
    )
    daemon = AutopilotDaemon(mk_config(tmp_path, candidates_file))

    called = {"feed": 0}

    def fake_feed_reference(**kwargs):  # type: ignore[no-untyped-def]
        called["feed"] += 1
        return {"status": "ok", **kwargs}

    daemon.adapter.feed_reference = fake_feed_reference  # type: ignore[assignment]
    summary = daemon.run_once(keystore_password="")

    assert called["feed"] == 1
    assert summary["executed"] == 1


def test_daemon_dispatch_adopt_prepare_only(tmp_path: Path) -> None:
    candidates_file = tmp_path / "candidates.json"
    candidates_file.write_text(
        json.dumps([mk_candidate("adopt", {"agent_id": 8, "opinion_id": 77, "submit_mode": "prepare_only"})]),
        encoding="utf-8",
    )
    daemon = AutopilotDaemon(mk_config(tmp_path, candidates_file))

    called = {"adopt": 0}

    def fake_memory_from_opinion(**kwargs):  # type: ignore[no-untyped-def]
        called["adopt"] += 1
        return {"status": "prepared", **kwargs}

    daemon.adapter.memory_from_opinion = fake_memory_from_opinion  # type: ignore[assignment]
    summary = daemon.run_once(keystore_password="")

    assert called["adopt"] == 1
    assert summary["executed"] == 1
