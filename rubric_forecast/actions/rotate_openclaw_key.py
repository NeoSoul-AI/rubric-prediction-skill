"""POST /v1/agents/{id}/openclaw-key/rotate."""

from __future__ import annotations

from rubric_forecast.actions.base import ActionContext, ActionResult
from rubric_forecast.openclaw_adapter import AdapterApiError


def run_rotate_openclaw_key(ctx: ActionContext) -> ActionResult:
    meta = ctx.candidate.get("meta") if isinstance(ctx.candidate.get("meta"), dict) else {}
    try:
        agent_id = meta.get("target_agent_id") or meta.get("agent_id")
        if agent_id is None:
            return ActionResult(ok=False, error="missing agent_id")
        result = ctx.daemon.adapter.rotate_openclaw_key(agent_id=agent_id)
        return ActionResult(ok=True, gas_used=0, detail={"result": result})
    except (AdapterApiError, TypeError, ValueError) as e:
        return ActionResult(ok=False, error=str(e))
