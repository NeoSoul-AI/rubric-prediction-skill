"""Build rubric engine payloads from scan candidates."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, MutableMapping, Sequence


def build_forecast_payload_from_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Map a generic candidate dict into `run_forecast` input.

    Expected keys (flexible):
    - question: str
    - options: list[str]
    - resolution_rule: str
    - evidence: list[dict]
    Optional: rubric_dimensions, normalization_temperature, base_option_scores
    """
    required = ("question", "options", "resolution_rule", "evidence")
    missing = [k for k in required if k not in candidate]
    if missing:
        raise ValueError(f"candidate missing keys: {missing}")

    payload: Dict[str, Any] = {
        "question": str(candidate["question"]),
        "options": list(candidate["options"]),
        "resolution_rule": str(candidate["resolution_rule"]),
        "evidence": list(candidate["evidence"]),
    }
    for opt in ("rubric_dimensions", "normalization_temperature", "base_option_scores",
                "forecast_time", "close_time", "monitoring_signals"):
        if opt in candidate:
            payload[opt] = candidate[opt]
    return payload


def normalized_top_score(forecast_result: Mapping[str, Any]) -> float:
    """Return top normalized score from engine output, or 0.0."""
    if forecast_result.get("status") != "ok":
        return 0.0
    scores = forecast_result.get("normalized_scores") or []
    if not scores:
        return 0.0
    best = 0.0
    for row in scores:
        if isinstance(row, dict) and "score" in row:
            try:
                best = max(best, float(row["score"]))
            except (TypeError, ValueError):
                continue
    return best


def candidate_id_from_payload(payload: Mapping[str, Any], index: int) -> str:
    """Stable id for dedup / cooldown keys."""
    extra = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    cid = extra.get("id") or extra.get("candidate_id")
    if cid is not None:
        return str(cid)
    return f"idx-{index}"
