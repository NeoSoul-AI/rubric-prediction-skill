"""Local autopilot daemon: scan -> forecast -> policy -> lifefun write actions."""

from __future__ import annotations

import json
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from rubric_forecast.audit import AuditLogger, new_run_id
from rubric_forecast.config import AutopilotConfig, forbid_plaintext_private_key_in_env
from rubric_forecast.engine import run_forecast
from rubric_forecast.executor.eoa_executor import EoaExecutor
from rubric_forecast.executor.session_key_executor import SessionKeyExecutor
from rubric_forecast.keystore import unlock_keystore
from rubric_forecast.openclaw_adapter import (
    AdapterApiError,
    OpenClawAdapter,
    predictions_to_candidate_payloads,
)
from rubric_forecast.planner import (
    build_forecast_payload_from_candidate,
    candidate_id_from_payload,
    normalized_top_score,
)
from rubric_forecast.policy import PolicyEngine, write_default_policy_file


class JobPhase(str, Enum):
    idle = "idle"
    scanning = "scanning"
    deciding = "deciding"
    executing = "executing"
    sleeping = "sleeping"


class AutopilotDaemon:
    """Poll OpenClaw/LifeFun, score with rubric engine, then dispatch actions."""

    def __init__(self, config: AutopilotConfig) -> None:
        self.config = config
        config.ensure_data_dir()
        self.policy = PolicyEngine(config.policy_path, config.state_path)
        self.audit = AuditLogger(config.audit_log_path)
        self.phase = JobPhase.idle
        self._executor: Any = None
        self.adapter = OpenClawAdapter(
            config.lifefun_api_base_url,
            jwt_token=config.resolved_jwt(),
        )

    def is_paused(self) -> bool:
        return self.config.pause_file_path.is_file()

    def pause(self) -> None:
        self.config.pause_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.pause_file_path.write_text("paused\n", encoding="utf-8")

    def resume(self) -> None:
        if self.config.pause_file_path.is_file():
            self.config.pause_file_path.unlink()

    def _load_candidates(self) -> List[Dict[str, Any]]:
        if self.config.candidates_file and self.config.candidates_file.is_file():
            raw = json.loads(self.config.candidates_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [x for x in raw if isinstance(x, dict)]
            if isinstance(raw, dict):
                c = raw.get("candidates")
                if isinstance(c, list):
                    return [x for x in c if isinstance(x, dict)]
        if not self.adapter.base_url:
            return []
        try:
            raw = self.adapter.list_predictions(
                status="open",
                chain_id=self.config.chain_id,
                limit=20,
            )
            return predictions_to_candidate_payloads(raw)
        except Exception as e:
            self.audit.log_event(
                run_id=new_run_id(),
                action_type="scan",
                status="error",
                chain_id=self.config.chain_id,
                error=str(e),
            )
            return []

    @staticmethod
    def _meta(cand: Dict[str, Any]) -> Dict[str, Any]:
        raw = cand.get("meta")
        return raw if isinstance(raw, dict) else {}

    def _resolve_action_type(self, cand: Dict[str, Any]) -> str:
        meta = self._meta(cand)
        action = meta.get("action_type") or cand.get("action_type") or "feed_reference"
        return str(action)

    def _dispatch_write_action(
        self,
        *,
        cand: Dict[str, Any],
        cid: str,
        run_id: str,
        top_score: float,
        final_answer: Any,
        executor: Optional[Any],
    ) -> bool:
        meta = self._meta(cand)
        action_type = self._resolve_action_type(cand)
        try:
            if action_type == "feed_reference":
                source_opinion_id = int(meta["source_opinion_id"])
                target_agent_id = int(meta["target_agent_id"])
                result = self.adapter.feed_reference(
                    source_opinion_id=source_opinion_id,
                    target_agent_id=target_agent_id,
                    note=str(meta.get("note")) if meta.get("note") is not None else None,
                    tx_hash=str(meta.get("tx_hash")) if meta.get("tx_hash") is not None else None,
                )
                self.audit.log_event(
                    run_id=run_id,
                    action_type=action_type,
                    status="ok",
                    chain_id=self.config.chain_id,
                    details={"candidate_id": cid, "top_score": top_score, "result": result},
                )
                return True

            if action_type == "adopt":
                target_agent_id = meta.get("target_agent_id") or meta.get("agent_id")
                if target_agent_id is None:
                    raise ValueError("adopt action missing target_agent_id/agent_id")
                opinion_id = int(meta["opinion_id"])
                result = self.adapter.memory_from_opinion(
                    agent_id=target_agent_id,
                    opinion_id=opinion_id,
                    reasoning_hash=(
                        str(meta.get("reasoning_hash"))
                        if meta.get("reasoning_hash") is not None
                        else None
                    ),
                    tx_hash=str(meta.get("tx_hash")) if meta.get("tx_hash") is not None else None,
                )
                submit_mode = str(meta.get("submit_mode", "prepare_only"))
                if submit_mode == "executor_call":
                    if executor is None:
                        raise RuntimeError(
                            "executor required for submit_mode=executor_call "
                            "(set AUTOPILOT_RPC_URL + keystore password)"
                        )
                    to_addr = meta.get("contract_address")
                    data_hex = meta.get("data_hex")
                    if not to_addr or not data_hex:
                        raise ValueError(
                            "executor_call requires meta.contract_address and meta.data_hex"
                        )
                    tx_result = executor.send_contract_call(
                        to=str(to_addr),
                        data_hex=str(data_hex),
                        value_wei=int(meta.get("value_wei", 0)),
                        gas_limit=(
                            int(meta["gas_limit"]) if meta.get("gas_limit") is not None else None
                        ),
                        function_name=str(meta.get("function_name", "intakeReasoning")),
                    )
                    if not tx_result.ok:
                        raise RuntimeError(tx_result.error or "executor call failed")
                self.audit.log_event(
                    run_id=run_id,
                    action_type=action_type,
                    status="ok",
                    chain_id=self.config.chain_id,
                    details={
                        "candidate_id": cid,
                        "top_score": top_score,
                        "final_answer": final_answer,
                        "result": result,
                        "submit_mode": submit_mode,
                    },
                )
                return True

            if action_type == "mint_confirm":
                target_agent_id = meta.get("target_agent_id") or meta.get("agent_id")
                if target_agent_id is None:
                    raise ValueError("mint_confirm missing target_agent_id/agent_id")
                tx_hash = str(meta["tx_hash"])
                result = self.adapter.confirm_mint(agent_id=target_agent_id, tx_hash=tx_hash)
                self.audit.log_event(
                    run_id=run_id,
                    action_type=action_type,
                    status="ok",
                    chain_id=self.config.chain_id,
                    details={"candidate_id": cid, "top_score": top_score, "result": result},
                )
                return True

            if action_type == "rotate_openclaw_key":
                target_agent_id = meta.get("target_agent_id") or meta.get("agent_id")
                if target_agent_id is None:
                    raise ValueError("rotate_openclaw_key missing target_agent_id/agent_id")
                result = self.adapter.rotate_openclaw_key(agent_id=target_agent_id)
                self.audit.log_event(
                    run_id=run_id,
                    action_type=action_type,
                    status="ok",
                    chain_id=self.config.chain_id,
                    details={"candidate_id": cid, "top_score": top_score, "result": result},
                )
                return True

            self.audit.log_event(
                run_id=run_id,
                action_type=action_type,
                status="skip",
                chain_id=self.config.chain_id,
                details={"candidate_id": cid, "reason": "unsupported action_type"},
            )
            return False
        except (KeyError, ValueError, AdapterApiError, RuntimeError) as e:
            self.audit.log_event(
                run_id=run_id,
                action_type=action_type,
                status="error",
                chain_id=self.config.chain_id,
                error=str(e),
                details={"candidate_id": cid},
            )
            return False

    def _ensure_executor(self, password: str) -> Any:
        if self._executor is not None:
            return self._executor
        if not self.config.rpc_url:
            return None
        unlocked = unlock_keystore(self.config.keystore_path, password)
        account = unlocked.account
        inner = EoaExecutor(rpc_url=self.config.rpc_url, chain_id=self.config.chain_id, account=account)
        allowed = self.policy.get_function_whitelist()
        mode = (self.config.execution_mode or "eoa").strip().lower()
        if mode == "session" or self.config.bundler_url:
            self._executor = SessionKeyExecutor(
                inner,
                allowed_functions=allowed,
                bundler_url=self.config.bundler_url,
            )
        else:
            self._executor = inner
        return self._executor

    def run_once(self, *, keystore_password: str) -> Dict[str, Any]:
        forbid_plaintext_private_key_in_env()
        self.policy.reload_policy()
        run_id = new_run_id()
        summary: Dict[str, Any] = {
            "run_id": run_id,
            "phase": JobPhase.scanning.value,
            "candidates": 0,
            "actions_planned": 0,
            "executed": 0,
            "skipped": [],
        }
        if self.is_paused():
            self.audit.log_event(
                run_id=run_id,
                action_type="cycle",
                status="skipped",
                details={"reason": "pause file present"},
            )
            summary["skipped"].append("paused")
            return summary

        if self.policy.is_halted():
            self.audit.log_event(
                run_id=run_id,
                action_type="cycle",
                status="halted",
                details={"reason": "circuit breaker"},
            )
            summary["skipped"].append("halted")
            return summary

        self.phase = JobPhase.scanning
        candidates = self._load_candidates()
        summary["candidates"] = len(candidates)

        self.phase = JobPhase.deciding
        for idx, cand in enumerate(candidates[: self.config.max_actions_per_cycle]):
            cid = candidate_id_from_payload(cand, idx)
            try:
                payload = build_forecast_payload_from_candidate(cand)
            except ValueError as e:
                self.audit.log_event(
                    run_id=run_id,
                    action_type="forecast",
                    status="error",
                    details={"candidate_id": cid, "error": str(e)},
                )
                continue

            result = run_forecast(payload)
            top = normalized_top_score(result)
            summary["actions_planned"] += 1

            if result.get("status") != "ok":
                self.audit.log_event(
                    run_id=run_id,
                    action_type="forecast",
                    status="insufficient",
                    details={"candidate_id": cid, "engine": result.get("engine")},
                )
                continue

            if top < self.config.min_normalized_score:
                self.audit.log_event(
                    run_id=run_id,
                    action_type="decision",
                    status="skip",
                    details={"candidate_id": cid, "top_score": top, "min": self.config.min_normalized_score},
                )
                continue

            pre = self.policy.check_action(
                chain_id=self.config.chain_id,
                action_type=self._resolve_action_type(cand),
                estimated_gas=21000,
                dedup_key=cid,
            )
            if not pre.allowed:
                self.audit.log_event(
                    run_id=run_id,
                    action_type="policy",
                    status="blocked",
                    details={"candidate_id": cid, "reasons": pre.reasons},
                )
                continue

            if self.config.dry_run:
                self.audit.log_event(
                    run_id=run_id,
                    action_type="execute",
                    status="dry_run",
                    chain_id=self.config.chain_id,
                    details={
                        "candidate_id": cid,
                        "top_score": top,
                        "final_answer": result.get("final_answer"),
                        "action_type": self._resolve_action_type(cand),
                        "meta": self._meta(cand),
                    },
                )
                summary["executed"] += 1
                continue

            self.phase = JobPhase.executing
            needs_executor = self._meta(cand).get("submit_mode") == "executor_call"
            ex = self._ensure_executor(keystore_password) if needs_executor else None
            if needs_executor and ex is None:
                self.audit.log_event(
                    run_id=run_id,
                    action_type=self._resolve_action_type(cand),
                    status="error",
                    error=(
                        "executor required by submit_mode=executor_call; "
                        "set AUTOPILOT_RPC_URL, keystore, AUTOPILOT_KEYSTORE_PASSWORD"
                    ),
                    details={"candidate_id": cid},
                )
                self.policy.record_failure()
                continue

            ok = self._dispatch_write_action(
                cand=cand,
                cid=cid,
                run_id=run_id,
                top_score=top,
                final_answer=result.get("final_answer"),
                executor=ex,
            )
            if ok:
                self.policy.record_action_committed(
                    self._resolve_action_type(cand),
                    21000,
                    dedup_key=cid,
                )
                self.policy.record_success()
                summary["executed"] += 1
            else:
                self.policy.record_failure()

        self.phase = JobPhase.idle
        return summary

    def loop_forever(self, *, keystore_password: str) -> None:
        while True:
            try:
                self.run_once(keystore_password=keystore_password)
            except Exception as e:
                self.audit.log_event(
                    run_id=new_run_id(),
                    action_type="cycle",
                    status="error",
                    error=str(e),
                )
                self.policy.record_failure()
            time.sleep(max(5, self.config.poll_interval_seconds))


def ensure_policy_file(path: Path) -> None:
    if not path.is_file():
        write_default_policy_file(path)
