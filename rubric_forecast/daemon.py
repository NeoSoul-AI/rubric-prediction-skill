"""Local autopilot daemon: scan -> forecast -> policy -> lifefun write actions."""

from __future__ import annotations

import json
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from rubric_forecast.actions.base import ActionContext
from rubric_forecast.actions.dispatch import dispatch_action, resolve_default_estimated_gas
from rubric_forecast.adapters.base import AdapterContext
from rubric_forecast.adapters.registry import get_adapter
from rubric_forecast.audit import AuditLogger, new_run_id
from rubric_forecast.config import AutopilotConfig, forbid_plaintext_private_key_in_env
from rubric_forecast.engine import run_forecast
from rubric_forecast.executor.contract_writer import ContractWriter
from rubric_forecast.executor.eoa_executor import EoaExecutor
from rubric_forecast.executor.selectors import selector_prefix
from rubric_forecast.executor.session_key_executor import SessionKeyExecutor
from rubric_forecast.keystore import unlock_keystore
from rubric_forecast.openclaw_adapter import OpenClawAdapter
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
        self.dapp_adapter = get_adapter(config.adapter_name)

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
            ctx = AdapterContext(config=self.config, api=self.adapter)
            return self.dapp_adapter.discover_candidates(ctx)
        except Exception as e:  # noqa: BLE001
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

    def _policy_gas_and_function(self, cand: Dict[str, Any]) -> tuple[int, Optional[str], Optional[str]]:
        action_type = self._resolve_action_type(cand)
        meta = self._meta(cand)
        fn: Optional[str] = None
        est = resolve_default_estimated_gas(action_type)
        if action_type == "mint_confirm":
            if meta.get("tx_hash"):
                est = 21_000
                fn = "generic"
            else:
                est = resolve_default_estimated_gas("mint_confirm")
                fn = "mintWithSig"
        elif action_type == "adopt" and (
            meta.get("auto_onchain") or meta.get("submit_mode") == "executor_call"
        ):
            est = resolve_default_estimated_gas("adopt")
            fn = str(meta.get("function_name", "intakeReasoning"))
        elif action_type == "adopt":
            est = 21_000
            fn = "generic"
        prefix = selector_prefix(fn) if fn else None
        return est, fn, prefix

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

    def run_once(
        self,
        *,
        keystore_password: str,
        candidates_override: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        forbid_plaintext_private_key_in_env()
        self.policy.reload_policy()
        self.adapter.set_jwt(self.config.resolved_jwt())
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
        candidates = candidates_override if candidates_override is not None else self._load_candidates()
        summary["candidates"] = len(candidates)

        ex: Any = None
        cw: Optional[ContractWriter] = None
        if (
            not self.config.dry_run
            and self.config.rpc_url
            and keystore_password
            and self.config.keystore_path.is_file()
        ):
            ex = self._ensure_executor(keystore_password)
            if ex is not None:
                cw = ContractWriter(
                    rpc_url=self.config.rpc_url,
                    chain_id=self.config.chain_id,
                    executor=ex,
                )

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

            forecast_result = run_forecast(payload)
            top = normalized_top_score(forecast_result)
            summary["actions_planned"] += 1

            if forecast_result.get("status") != "ok":
                self.audit.log_event(
                    run_id=run_id,
                    action_type="forecast",
                    status="insufficient",
                    details={"candidate_id": cid, "engine": forecast_result.get("engine")},
                )
                continue

            if top < self.config.min_normalized_score:
                self.audit.log_event(
                    run_id=run_id,
                    action_type="decision",
                    status="skip",
                    details={
                        "candidate_id": cid,
                        "top_score": top,
                        "min": self.config.min_normalized_score,
                    },
                )
                continue

            action_type = self._resolve_action_type(cand)
            est, fn_hint, calldata_pfx = self._policy_gas_and_function(cand)
            pre = self.policy.check_action(
                chain_id=self.config.chain_id,
                action_type=action_type,
                to_address=None,
                function_name=fn_hint or "generic",
                estimated_gas=est,
                dedup_key=cid,
                calldata_prefix=calldata_pfx,
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
                        "final_answer": forecast_result.get("final_answer"),
                        "action_type": action_type,
                        "meta": self._meta(cand),
                    },
                )
                summary["executed"] += 1
                continue

            self.phase = JobPhase.executing
            meta = self._meta(cand)
            needs_ex = meta.get("submit_mode") == "executor_call" or (
                action_type == "mint_confirm" and not meta.get("tx_hash")
            )
            needs_ex = needs_ex or (action_type == "adopt" and bool(meta.get("auto_onchain")))
            if needs_ex and ex is None:
                self.audit.log_event(
                    run_id=run_id,
                    action_type=action_type,
                    status="error",
                    error=(
                        "on-chain executor required; set AUTOPILOT_RPC_URL, import keystore, "
                        "AUTOPILOT_KEYSTORE_PASSWORD"
                    ),
                    details={"candidate_id": cid},
                )
                self.policy.record_failure()
                continue

            actx = ActionContext(
                daemon=self,
                candidate=cand,
                cid=cid,
                run_id=run_id,
                top_score=top,
                final_answer=forecast_result.get("final_answer"),
                executor=ex,
                contract_writer=cw,
            )
            out = dispatch_action(actx)
            if out.ok:
                gas_used = int(out.gas_used or 0)
                self.policy.record_action_committed(action_type, gas_used, dedup_key=cid)
                self.policy.record_success()
                summary["executed"] += 1
                self.audit.log_event(
                    run_id=run_id,
                    action_type=action_type,
                    status="ok",
                    chain_id=self.config.chain_id,
                    details={
                        "candidate_id": cid,
                        "top_score": top,
                        "gas_used": gas_used,
                        **(out.detail or {}),
                    },
                )
            else:
                self.audit.log_event(
                    run_id=run_id,
                    action_type=action_type,
                    status="error",
                    chain_id=self.config.chain_id,
                    error=out.error or "action failed",
                    details={"candidate_id": cid},
                )
                self.policy.record_failure()

        self.phase = JobPhase.idle
        return summary

    def loop_forever(self, *, keystore_password: str) -> None:
        while True:
            try:
                self.run_once(keystore_password=keystore_password)
            except Exception as e:  # noqa: BLE001
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
