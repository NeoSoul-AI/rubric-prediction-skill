"""Mint: GET mint_with_sig + on-chain mintWithSig + POST confirm, or confirm only if tx_hash set."""

from __future__ import annotations

from typing import Any, Dict

from rubric_forecast.actions.base import ActionContext, ActionResult
from rubric_forecast.adapters.lifefun import agent_mint_from_dict, parse_mint_with_sig_from_response
from rubric_forecast.openclaw_adapter import AdapterApiError


def run_mint_confirm(ctx: ActionContext) -> ActionResult:
    meta = ctx.candidate.get("meta") if isinstance(ctx.candidate.get("meta"), dict) else {}
    try:
        target_agent_id = meta.get("target_agent_id") or meta.get("agent_id")
        if target_agent_id is None:
            return ActionResult(ok=False, error="mint_confirm missing target_agent_id/agent_id")

        gas_used = 0
        tx_hash: str | None = None
        if meta.get("tx_hash"):
            tx_hash = str(meta["tx_hash"])
        else:
            if ctx.contract_writer is None or ctx.executor is None:
                return ActionResult(
                    ok=False,
                    error="mint_confirm without tx_hash requires keystore + RPC + executor",
                )
            api_body = ctx.daemon.adapter.get_mint_payload(target_agent_id)
            parsed = parse_mint_with_sig_from_response(api_body if isinstance(api_body, dict) else {})
            if parsed is None and isinstance(api_body, dict) and isinstance(api_body.get("mint_with_sig"), dict):
                parsed = agent_mint_from_dict(api_body["mint_with_sig"])
            if parsed is None:
                return ActionResult(ok=False, error="mint_with_sig missing from GET /v1/agents/{id}/mint")
            try:
                est = ctx.contract_writer.estimate_mint_gas(parsed)
            except Exception as e:  # noqa: BLE001
                return ActionResult(ok=False, error=f"gas estimate failed: {e}")
            tx_result = ctx.contract_writer.send_mint_with_sig(parsed, gas_limit=est)
            if not tx_result.ok:
                return ActionResult(ok=False, error=tx_result.error or "mintWithSig failed")
            gas_used = int((tx_result.receipt or {}).get("gasUsed", 0) or est)
            tx_hash = tx_result.tx_hash

        if not tx_hash:
            return ActionResult(ok=False, error="no tx_hash for confirm")
        confirm = ctx.daemon.adapter.confirm_mint(agent_id=target_agent_id, tx_hash=tx_hash)
        detail: Dict[str, Any] = {"tx_hash": tx_hash, "confirm": confirm}
        return ActionResult(ok=True, gas_used=gas_used, detail=detail)
    except (KeyError, ValueError, AdapterApiError, RuntimeError) as e:
        return ActionResult(ok=False, error=str(e))
