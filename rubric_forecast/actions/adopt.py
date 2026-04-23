"""Adopt: memory_from_opinion + optional intakeReasoning on chain."""

from __future__ import annotations

from typing import Any, Dict

from rubric_forecast.actions.base import ActionContext, ActionResult
from rubric_forecast.adapters.lifefun import reasoning_intake_from_dict
from rubric_forecast.openclaw_adapter import AdapterApiError


def run_adopt(ctx: ActionContext) -> ActionResult:
    meta = ctx.candidate.get("meta") if isinstance(ctx.candidate.get("meta"), dict) else {}
    try:
        target_agent_id = meta.get("target_agent_id") or meta.get("agent_id")
        if target_agent_id is None:
            return ActionResult(ok=False, error="adopt missing target_agent_id/agent_id")
        opinion_id = int(meta["opinion_id"])
        result: Dict[str, Any] = ctx.daemon.adapter.memory_from_opinion(
            agent_id=target_agent_id,
            opinion_id=opinion_id,
            reasoning_hash=str(meta["reasoning_hash"]) if meta.get("reasoning_hash") is not None else None,
            tx_hash=str(meta["tx_hash"]) if meta.get("tx_hash") is not None else None,
        )
        submit_mode = str(meta.get("submit_mode", "prepare_only"))
        gas_used = 0
        tx_hash: str | None = None

        if submit_mode == "executor_call":
            if ctx.executor is None:
                return ActionResult(ok=False, error="executor required for submit_mode=executor_call")
            to_addr = meta.get("contract_address")
            data_hex = meta.get("data_hex")
            if not to_addr or not data_hex:
                return ActionResult(
                    ok=False,
                    error="executor_call requires meta.contract_address and meta.data_hex",
                )
            fn = str(meta.get("function_name", "intakeReasoning"))
            tx_result = ctx.executor.send_contract_call(
                to=str(to_addr),
                data_hex=str(data_hex),
                value_wei=int(meta.get("value_wei", 0)),
                gas_limit=int(meta["gas_limit"]) if meta.get("gas_limit") is not None else None,
                function_name=fn,
            )
            if not tx_result.ok:
                return ActionResult(ok=False, error=tx_result.error or "executor call failed")
            gas_used = int((tx_result.receipt or {}).get("gasUsed", 0) or 450_000)
            tx_hash = tx_result.tx_hash

        elif meta.get("auto_onchain") and ctx.contract_writer is not None:
            raw_sig = result.get("reasoning_intake_with_sig")
            if not isinstance(raw_sig, dict):
                return ActionResult(ok=False, error="reasoning_intake_with_sig missing from API response")
            payload = reasoning_intake_from_dict(raw_sig)
            try:
                est = ctx.contract_writer.estimate_intake_gas(payload)
            except Exception as e:  # noqa: BLE001
                return ActionResult(ok=False, error=f"gas estimate failed: {e}")
            tx_result = ctx.contract_writer.send_intake_reasoning(payload, gas_limit=est)
            if not tx_result.ok:
                return ActionResult(ok=False, error=tx_result.error or "intakeReasoning failed")
            gas_used = int((tx_result.receipt or {}).get("gasUsed", 0) or est)
            tx_hash = tx_result.tx_hash

        detail: Dict[str, Any] = {
            "prepare": result,
            "submit_mode": submit_mode,
            "tx_hash": tx_hash,
        }
        return ActionResult(ok=True, gas_used=gas_used, detail=detail)
    except (KeyError, ValueError, AdapterApiError, RuntimeError) as e:
        return ActionResult(ok=False, error=str(e))
