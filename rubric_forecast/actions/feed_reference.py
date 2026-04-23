"""POST /v1/references/feed."""

from __future__ import annotations

from rubric_forecast.actions.base import ActionContext, ActionResult
from rubric_forecast.openclaw_adapter import AdapterApiError


def run_feed_reference(ctx: ActionContext) -> ActionResult:
    meta = ctx.candidate.get("meta") if isinstance(ctx.candidate.get("meta"), dict) else {}
    try:
        source_opinion_id = int(meta["source_opinion_id"])
        target_agent_id = int(meta["target_agent_id"])
        result = ctx.daemon.adapter.feed_reference(
            source_opinion_id=source_opinion_id,
            target_agent_id=target_agent_id,
            note=str(meta.get("note")) if meta.get("note") is not None else None,
            tx_hash=str(meta.get("tx_hash")) if meta.get("tx_hash") is not None else None,
        )
        return ActionResult(ok=True, gas_used=0, detail={"result": result})
    except (KeyError, ValueError, AdapterApiError) as e:
        return ActionResult(ok=False, error=str(e))
