"""Route autopilot write actions to handlers."""

from __future__ import annotations

from typing import Dict

from rubric_forecast.actions.adopt import run_adopt
from rubric_forecast.actions.base import ActionContext, ActionResult
from rubric_forecast.actions.feed_reference import run_feed_reference
from rubric_forecast.actions.mint_confirm import run_mint_confirm
from rubric_forecast.actions.predict_only import run_predict_only
from rubric_forecast.actions.rotate_openclaw_key import run_rotate_openclaw_key


def dispatch_action(ctx: ActionContext) -> ActionResult:
    action = ctx.daemon._resolve_action_type(ctx.candidate)  # noqa: SLF001
    if action == "predict_only":
        return run_predict_only(ctx)
    if action == "feed_reference":
        return run_feed_reference(ctx)
    if action == "adopt":
        return run_adopt(ctx)
    if action == "mint_confirm":
        return run_mint_confirm(ctx)
    if action == "rotate_openclaw_key":
        return run_rotate_openclaw_key(ctx)
    return ActionResult(ok=False, error=f"unsupported action_type: {action}")


def resolve_default_estimated_gas(action_type: str) -> int:
    """Planning gas hint before execution (conservative)."""
    m: Dict[str, int] = {
        "predict_only": 21_000,
        "feed_reference": 21_000,
        "rotate_openclaw_key": 21_000,
        "mint_confirm": 450_000,
        "adopt": 450_000,
    }
    return int(m.get(action_type, 21_000))
