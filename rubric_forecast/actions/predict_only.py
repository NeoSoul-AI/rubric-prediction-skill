"""Forecast-only candidate: no backend write."""

from __future__ import annotations

from rubric_forecast.actions.base import ActionContext, ActionResult


def run_predict_only(ctx: ActionContext) -> ActionResult:
    return ActionResult(
        ok=True,
        gas_used=0,
        detail={"mode": "predict_only", "final_answer": ctx.final_answer},
    )
