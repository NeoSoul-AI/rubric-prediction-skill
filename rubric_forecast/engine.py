#!/usr/bin/env python3
"""
Deterministic rubric forecasting engine.

Usage:
  python -m rubric_forecast --input input.json
  python scripts/rubric_forecast.py --input input.json
  cat input.json | python -m rubric_forecast
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ENGINE_NAME = "rubric-forecast-engine"
ENGINE_VERSION = "2.2.0"
ENGINE_STRICT_DECOUPLING = True

STRENGTH_FACTOR_MAP: Dict[str, float] = {
    "weak": 0.6,
    "medium": 1.0,
    "strong": 1.6,
    "extreme": 2.2,
}

DEFAULT_NORMALIZATION_TEMPERATURE = 1.0

LEGACY_DIMENSIONS: List[Dict[str, Any]] = [
    {
        "key": "reliability",
        "label": "Reliability",
        "weight": 0.35,
        "why_it_matters": "Whether the evidence source is credible and verifiable.",
    },
    {
        "key": "mechanism_fit",
        "label": "Mechanism fit",
        "weight": 0.25,
        "why_it_matters": "Whether there is a clear causal link between the evidence and the outcome.",
    },
    {
        "key": "novelty",
        "label": "Novelty",
        "weight": 0.20,
        "why_it_matters": "Whether the information adds new decision value.",
    },
    {
        "key": "timeliness",
        "label": "Timeliness",
        "weight": 0.20,
        "why_it_matters": "Whether the information is close enough to the current decision context.",
    },
]


def _clip_01(value: Any, default: float = 0.5) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, n))


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _read_input(path: Optional[str]) -> Any:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.load(sys.stdin)


def _input_sha256(payload: Any) -> Optional[str]:
    if payload is None:
        return None
    try:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _engine_metadata(payload: Any) -> Dict[str, Any]:
    return {
        "name": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "strict_decoupling": ENGINE_STRICT_DECOUPLING,
        "computed_by": "rubric_forecast.engine",
        "input_sha256": _input_sha256(payload),
    }


def _insufficient(
    payload: Any,
    missing_fields: Optional[List[str]] = None,
    blocking_reasons: Optional[List[str]] = None,
    next_required_inputs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    missing_fields = missing_fields or []
    return {
        "status": "insufficient_spec",
        "missing_fields": missing_fields,
        "blocking_reasons": blocking_reasons or [],
        "next_required_inputs": next_required_inputs or missing_fields,
        "engine": _engine_metadata(payload),
    }


def _slugify_key(value: str, fallback_index: int) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or f"dimension-{fallback_index}"


def _normalize_dimensions(raw: Any, assumptions: List[str]) -> List[Dict[str, Any]]:
    dims: List[Dict[str, Any]] = []

    if isinstance(raw, list) and len(raw) >= 2:
        seen_keys = set()
        for idx, row in enumerate(raw):
            if not isinstance(row, dict):
                assumptions.append(f"rubric_dimensions[{idx}] invalid and ignored")
                continue
            label = str(row.get("label") or row.get("key") or f"Dimension {idx + 1}").strip()
            key = str(row.get("key") or _slugify_key(label, idx + 1)).strip()
            if not key:
                key = f"dimension-{idx + 1}"
            if key in seen_keys:
                assumptions.append(f"rubric_dimensions[{idx}] duplicate key '{key}' and ignored")
                continue
            seen_keys.add(key)
            try:
                weight = float(row.get("weight", 0))
            except (TypeError, ValueError):
                weight = 0.0
                assumptions.append(f"rubric_dimensions[{idx}].weight invalid; set to 0")
            if weight < 0:
                assumptions.append(f"rubric_dimensions[{idx}].weight negative; clamped to 0")
                weight = 0.0
            dims.append(
                {
                    "key": key,
                    "label": label,
                    "weight": weight,
                    "why_it_matters": str(row.get("why_it_matters", "")).strip(),
                }
            )

    if len(dims) < 2:
        assumptions.append(
            "rubric_dimensions missing or invalid; legacy fallback dimensions applied"
        )
        dims = [dict(row) for row in LEGACY_DIMENSIONS]

    total = sum(float(row["weight"]) for row in dims)
    if total <= 0:
        assumptions.append("rubric_dimensions total weight <= 0; equal weights applied")
        equal = 1.0 / len(dims)
        for row in dims:
            row["weight"] = equal
        return dims

    for row in dims:
        row["weight"] = float(row["weight"]) / total
    return dims


def _coerce_base_option_scores(
    raw: Any, options: Sequence[str], assumptions: List[str]
) -> Dict[str, float]:
    option_set = set(options)
    scores: Dict[str, float] = {opt: 0.0 for opt in options}

    if raw is None:
        return scores

    if isinstance(raw, dict):
        for opt, value in raw.items():
            if opt not in option_set:
                continue
            try:
                scores[opt] = float(value)
            except (TypeError, ValueError):
                assumptions.append(f"base_option_scores.{opt} invalid; defaulted to 0")
    elif isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            opt = row.get("option")
            if opt not in option_set:
                continue
            try:
                scores[str(opt)] = float(row.get("score"))
            except (TypeError, ValueError):
                assumptions.append(f"base_option_scores for {opt} invalid; defaulted to 0")
    else:
        assumptions.append("base_option_scores invalid; defaulted to all zeros")

    return scores


def _extract_target_options(value: Any) -> List[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _normalize_temperature(raw: Any, assumptions: List[str]) -> float:
    if raw is None:
        return DEFAULT_NORMALIZATION_TEMPERATURE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        assumptions.append("normalization_temperature invalid; default applied")
        return DEFAULT_NORMALIZATION_TEMPERATURE
    if value <= 0:
        assumptions.append("normalization_temperature <= 0; default applied")
        return DEFAULT_NORMALIZATION_TEMPERATURE
    return value


def _normalized_scores(
    raw_scores: Dict[str, float], temperature: float
) -> Dict[str, float]:
    max_score = max(raw_scores.values())
    exp_scores = {
        key: math.exp((value - max_score) / temperature)
        for key, value in raw_scores.items()
    }
    total = sum(exp_scores.values())
    if total <= 0:
        n = len(exp_scores)
        return {key: 1.0 / n for key in exp_scores}
    return {key: exp_scores[key] / total for key in exp_scores}


def _format_score_rows(values: Dict[str, float], key: str) -> List[Dict[str, Any]]:
    return [{"option": opt, key: round(values[opt], 6)} for opt in values]


def _compute_with_ledger(
    options: Sequence[str],
    base_scores: Dict[str, float],
    evidence_ledger: Sequence[Dict[str, Any]],
    temperature: float,
    exclude_evidence_ids: Optional[Iterable[str]] = None,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    excluded = set(exclude_evidence_ids or [])
    raw_scores = {opt: float(base_scores[opt]) for opt in options}

    for row in evidence_ledger:
        if row["id"] in excluded:
            continue
        contribution = float(row["contribution"])
        for target in row["targets"]:
            raw_scores[target] += contribution

    normalized = _normalized_scores(raw_scores, temperature=temperature)
    return raw_scores, normalized


def _detect_conflicts(ledger: Sequence[Dict[str, Any]]) -> List[str]:
    option_flags: Dict[str, Dict[str, bool]] = {}
    notes: List[str] = []
    for row in ledger:
        if row["strength"] not in ("strong", "extreme"):
            continue
        for target in row["targets"]:
            flag = option_flags.setdefault(target, {"for": False, "against": False})
            flag[row["stance"]] = True

    for option, flag in option_flags.items():
        if flag["for"] and flag["against"]:
            notes.append(
                f"strong conflict on option '{option}': both supporting and opposing strong evidence present"
            )
    return notes


def _rank_options(
    options: Sequence[str], normalized: Dict[str, float], raw_scores: Dict[str, float]
) -> List[str]:
    return sorted(
        options, key=lambda opt: (normalized[opt], raw_scores[opt], opt), reverse=True
    )


def _position_label(rank_idx: int) -> str:
    if rank_idx == 0:
        return "Leading"
    if rank_idx == 1:
        return "Runner-up"
    return "Trailing"


def _top_rows_for_option(
    ledger: Sequence[Dict[str, Any]], option: str, positive: bool, limit: int = 2
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in ledger:
        if option not in row["targets"]:
            continue
        contribution = float(row["contribution"])
        if positive and contribution > 0:
            rows.append(row)
        if (not positive) and contribution < 0:
            rows.append(row)
    rows.sort(key=lambda item: abs(float(item["contribution"])), reverse=True)
    return rows[:limit]


def _rows_for_dimension(
    ledger: Sequence[Dict[str, Any]], option: str, dim_key: str, limit: int = 2
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in ledger:
        if option not in row["targets"]:
            continue
        dim_value = float(row["dimension_contributions"].get(dim_key, 0.0))
        if abs(dim_value) <= 0:
            continue
        rows.append(row)
    rows.sort(
        key=lambda item: abs(float(item["dimension_contributions"].get(dim_key, 0.0))),
        reverse=True,
    )
    return rows[:limit]


def _format_evidence_line(row: Dict[str, Any]) -> str:
    contribution = float(row["contribution"])
    direction = "support" if contribution >= 0 else "oppose"
    claim = str(row.get("claim", "")).strip()
    source = str(row.get("source", "")).strip()
    reason = claim if claim else (source if source else "No summary provided")
    return f"{row['id']} ({direction}, contribution {contribution:+.3f}): {reason}"


def _compact_evidence_phrase(row: Dict[str, Any], max_len: int = 88) -> str:
    contribution = float(row["contribution"])
    claim = str(row.get("claim", "")).strip()
    source = str(row.get("source", "")).strip()
    text = claim if claim else (source if source else "No summary provided")
    if len(text) > max_len:
        text = text[: max_len - 1] + "..."
    return f"{row['id']}({contribution:+.3f}) {text}"


def _dimension_assessment(value: float, threshold: float) -> str:
    if abs(value) <= threshold:
        return "Neutral impact"
    if value > 0:
        return "Positive driver"
    return "Negative drag"


def _option_contribution_totals(
    ledger: Sequence[Dict[str, Any]], option: str
) -> Tuple[float, float]:
    pos = 0.0
    neg = 0.0
    for row in ledger:
        if option not in row["targets"]:
            continue
        contribution = float(row["contribution"])
        if contribution >= 0:
            pos += contribution
        else:
            neg += abs(contribution)
    return pos, neg


def _dependency_narrative(ledger: Sequence[Dict[str, Any]]) -> str:
    group_to_rows: Dict[str, List[Dict[str, Any]]] = {}
    for row in ledger:
        group = row.get("dependency_group")
        if isinstance(group, str) and group:
            group_to_rows.setdefault(group, []).append(row)

    notes: List[str] = []
    for group, rows in group_to_rows.items():
        penalized = [row for row in rows if float(row.get("dependency_penalty", 1.0)) < 1.0]
        if penalized:
            penal_ids = ", ".join(str(row["id"]) for row in penalized)
            notes.append(f"In group {group}, {penal_ids} triggered a correlation penalty")
    if not notes:
        return "No duplicate sources in this evidence set required an extra correlation penalty."
    return "; ".join(notes) + "."


def _weights_summary(dimensions: Sequence[Dict[str, Any]]) -> str:
    return ", ".join(
        f"{dim['label']} {float(dim['weight']):.0%}" for dim in dimensions
    )


def _clean_sentence_tail(text: str) -> str:
    return text.strip().rstrip("。；;，,.!?")


def _natural_join(parts: Sequence[str]) -> str:
    items = [part for part in parts if isinstance(part, str) and part]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _claim_summary(row: Dict[str, Any], max_len: int = 100) -> str:
    claim = str(row.get("claim", "")).strip()
    source = str(row.get("source", "")).strip()
    text = claim if claim else (source if source else "No summary provided")
    text = _clean_sentence_tail(text)
    if len(text) > max_len:
        text = text[: max_len - 1] + "..."
    return text


def _lead_descriptor(gap: float) -> str:
    if gap < 0.08:
        return "is only slightly ahead"
    if gap < 0.18:
        return "is modestly ahead"
    if gap < 0.30:
        return "has a meaningful edge"
    return "is clearly ahead"


def _confidence_descriptor(top_score: float) -> str:
    if top_score < 0.58:
        return "overall the call remains very tight"
    if top_score < 0.70:
        return "the conclusion tilts one way, but disagreement is still material"
    if top_score < 0.82:
        return "the conclusion has a fairly clear edge"
    return "the conclusion has a strong edge"


def _top_dimensions_by_weight(
    dimensions: Sequence[Dict[str, Any]], limit: int = 3
) -> List[Dict[str, Any]]:
    return sorted(dimensions, key=lambda dim: float(dim["weight"]), reverse=True)[:limit]


def _probability_phrase(value: float) -> str:
    if value < 0.08:
        return "well under ~10%"
    if value < 0.18:
        return "a bit over ~10%"
    if value < 0.28:
        return "roughly low-20s %"
    if value < 0.38:
        return "roughly mid-30s %"
    if value < 0.48:
        return "~40%"
    if value < 0.58:
        return "a coin-flip level"
    if value < 0.68:
        return "~60%"
    if value < 0.78:
        return "~70%"
    if value < 0.88:
        return "~80%"
    return "near decisive"


def _dominant_dimension_labels(
    dim_totals: Dict[str, float],
    dimensions: Sequence[Dict[str, Any]],
    limit: int = 2,
) -> List[str]:
    ranked = sorted(
        dimensions,
        key=lambda dim: abs(float(dim_totals.get(dim["key"], 0.0))),
        reverse=True,
    )
    labels = [str(dim["label"]) for dim in ranked if abs(float(dim_totals.get(dim["key"], 0.0))) > 0]
    return labels[:limit]


def _rubric_design_summary(dimensions: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for dim in dimensions:
        why = str(dim.get("why_it_matters", "")).strip()
        if why:
            parts.append(f"{dim['label']}: {why}")
        else:
            parts.append(
                f"{dim['label']}: captures one key facet of judgment for this question."
            )
    return "Topic-specific rubric dimensions: " + "; ".join(parts)


def _build_reasoning_narrative(
    question: str,
    dimensions: Sequence[Dict[str, Any]],
    options: Sequence[str],
    raw_scores: Dict[str, float],
    normalized: Dict[str, float],
    evidence_ledger: Sequence[Dict[str, Any]],
    option_dimension_totals: Dict[str, Dict[str, float]],
    conflict_notes: Sequence[str],
    sensitivity: Sequence[Dict[str, Any]],
) -> str:
    ranked = _rank_options(options, normalized, raw_scores)
    top_option = ranked[0]
    second_option = ranked[1] if len(ranked) > 1 else None
    top_dims = _top_dimensions_by_weight(dimensions, limit=min(3, len(dimensions)))
    top_dim_labels = [str(dim["label"]) for dim in top_dims]
    focus_sentences = [
        f"The real disagreement centers on {_natural_join(top_dim_labels)}. "
    ]
    for dim in top_dims[:2]:
        why = _clean_sentence_tail(str(dim.get("why_it_matters", "")).strip())
        if why:
            focus_sentences.append(f"{dim['label']} matters: {why}. ")
    p1 = "".join(focus_sentences)

    top_support = _top_rows_for_option(evidence_ledger, top_option, positive=True, limit=2)
    top_support_text = (
        "; ".join(f"{row['id']}: {_claim_summary(row)}" for row in top_support)
        if top_support
        else "There is not yet strong enough positive evidence."
    )
    top_dims_winner = _dominant_dimension_labels(
        option_dimension_totals.get(top_option, {}), dimensions, limit=2
    )
    winner_prob = _probability_phrase(normalized[top_option])
    p2 = (
        f"The scenario that currently fits best is {top_option}. "
        f"The main evidence pushing the call that way is {top_support_text}. "
        f"They jointly reinforce {_natural_join(top_dims_winner) or 'the core dimensions'}, "
        f"making this narrative the primary path at roughly {winner_prob} weight."
    )

    p3 = ""
    if second_option is not None:
        second_support = _top_rows_for_option(
            evidence_ledger, second_option, positive=True, limit=2
        )
        second_support_text = (
            "; ".join(f"{row['id']}: {_claim_summary(row)}" for row in second_support)
            if second_support
            else "Coherent supporting evidence is still thin."
        )
        second_dims = _dominant_dimension_labels(
            option_dimension_totals.get(second_option, {}), dimensions, limit=2
        )
        second_prob = _probability_phrase(normalized[second_option])
        p3 = (
            f"However, {second_option} is far from ruled out. "
            f"The core constraints on the other side come from {second_support_text}. "
            f"These mainly bite on {_natural_join(second_dims) or 'another set of key dimensions'}, "
            f"so the counter-scenario still carries about {second_prob} weight."
        )

    dependency_note = _dependency_narrative(evidence_ledger)
    if conflict_notes:
        conflict_text = "; ".join(conflict_notes)
        p4 = (
            f"From the evidence structure: {dependency_note} "
            f"Additional conflicts to monitor: {conflict_text}."
        )
    else:
        p4 = (
            f"From the evidence structure: {dependency_note} "
            f"Nothing yet amounts to a strong conflict that would flip the main call."
        )

    if sensitivity:
        row = sensitivity[0]
        delta = abs(float(row["delta_top_score"]))
        if delta >= 0.20:
            sensitivity_text = (
                f"On sensitivity: dropping key evidence {row['drop_evidence_id']} "
                "materially narrows the lead, so this judgment still leans on that main narrative."
            )
        elif delta >= 0.10:
            sensitivity_text = (
                f"On sensitivity: removing {row['drop_evidence_id']} "
                "narrows the edge but is unlikely to flip the call immediately."
            )
        else:
            sensitivity_text = (
                f"On sensitivity: removing {row['drop_evidence_id']} "
                "barely moves the overall read; the main conclusion looks relatively robust."
            )
    else:
        sensitivity_text = "Not enough valid evidence to rerun sensitivity meaningfully."

    gap = normalized[top_option] - normalized[second_option] if second_option else 0.0
    conclusion = (
        f"A reasonable read: {top_option} {_lead_descriptor(gap)}; "
        f"{_confidence_descriptor(normalized[top_option])}."
    )
    if second_option is not None and sensitivity:
        watch_dims = _dominant_dimension_labels(
            option_dimension_totals.get(second_option, {}), dimensions, limit=2
        )
        if watch_dims:
            conclusion += (
                f" New evidence on {_natural_join(watch_dims)} is most likely to move the call next."
            )
    p5 = sensitivity_text + " " + conclusion

    paragraphs = [p1, p2]
    if p3:
        paragraphs.append(p3)
    paragraphs.extend([p4, p5])
    return "\n\n".join(paragraphs)


def _build_multidim_analysis(
    question: str,
    dimensions: Sequence[Dict[str, Any]],
    raw_scores: Dict[str, float],
    normalized: Dict[str, float],
    evidence_ledger: Sequence[Dict[str, Any]],
    option_dimension_totals: Dict[str, Dict[str, float]],
    sensitivity: Sequence[Dict[str, Any]],
    conflict_notes: Sequence[str],
) -> Dict[str, Any]:
    ranked = _rank_options(list(raw_scores.keys()), normalized, raw_scores)
    top_option = ranked[0]
    second_option = ranked[1] if len(ranked) > 1 else None
    gap = normalized[top_option] - normalized[second_option] if second_option else 0.0
    top_support_count = len(
        _top_rows_for_option(evidence_ledger, top_option, positive=True, limit=999)
    )

    method_overview = (
        "This question uses a topic-specific rubric rather than a fixed template. "
        f"Dimensions and weights: {_weights_summary(dimensions)}. "
        "Each evidence item is scored on these dimensions; contributions combine "
        "strength factors and correlation penalties."
    )
    design_summary = _rubric_design_summary(dimensions)
    decision_summary = (
        f"The top-scoring option is {top_option} with normalized score {normalized[top_option]:.3f}."
        + (
            f" It leads {second_option} by {gap:.3f}."
            if second_option is not None
            else ""
        )
        + f" The call is mainly driven by {top_support_count} key supporting evidence items."
    )
    reasoning_narrative = _build_reasoning_narrative(
        question=question,
        dimensions=dimensions,
        options=list(raw_scores.keys()),
        raw_scores=raw_scores,
        normalized=normalized,
        evidence_ledger=evidence_ledger,
        option_dimension_totals=option_dimension_totals,
        conflict_notes=conflict_notes,
        sensitivity=sensitivity,
    )

    option_analyses: List[Dict[str, Any]] = []
    for rank_idx, option in enumerate(ranked):
        support_rows = _top_rows_for_option(evidence_ledger, option, positive=True, limit=2)
        oppose_rows = _top_rows_for_option(evidence_ledger, option, positive=False, limit=2)
        support_lines = [_format_evidence_line(row) for row in support_rows]
        oppose_lines = [_format_evidence_line(row) for row in oppose_rows]

        dim_totals = option_dimension_totals.get(
            option, {dim["key"]: 0.0 for dim in dimensions}
        )
        max_abs_dim = max(
            (abs(float(dim_totals.get(dim["key"], 0.0))) for dim in dimensions),
            default=0.0,
        )
        threshold = max(0.02, 0.12 * max_abs_dim)
        dimension_analysis: List[Dict[str, Any]] = []
        for dim in dimensions:
            dim_key = dim["key"]
            dim_value = float(dim_totals.get(dim_key, 0.0))
            assessment = _dimension_assessment(dim_value, threshold)
            key_rows = _rows_for_dimension(evidence_ledger, option, dim_key, limit=2)
            key_ids = [str(row["id"]) for row in key_rows]
            dimension_analysis.append(
                {
                    "dimension": dim_key,
                    "label": dim["label"],
                    "assessment": assessment,
                    "analysis": (
                        f"Net contribution on {dim['label']} {dim_value:+.3f}, assessed as {assessment}. "
                        f"Key evidence: "
                        + (", ".join(key_ids) if key_ids else "none.")
                    ),
                }
            )

        risk_notes: List[str] = []
        if rank_idx == 0 and sensitivity:
            delta = float(sensitivity[0]["delta_top_score"])
            if abs(delta) >= 0.20:
                risk_notes.append(
                    "Sensitive to one key evidence item; if it fails, the current lead could shrink materially."
                )
        if any(option in note for note in conflict_notes):
            risk_notes.append(
                "Strong conflicting evidence for this option; keep monitoring new information."
            )
        if not risk_notes:
            risk_notes.append("No obvious structural risks identified at present.")

        option_analyses.append(
            {
                "option": option,
                "position": _position_label(rank_idx),
                "score_statement": (
                    f"Raw score {raw_scores[option]:.3f}, normalized score {normalized[option]:.3f}."
                ),
                "dimension_analysis": dimension_analysis,
                "evidence_logic": {
                    "supporting": support_lines or ["No material supporting evidence found."],
                    "opposing": oppose_lines or ["No material opposing evidence found."],
                },
                "risk_note": " ".join(risk_notes),
            }
        )

    return {
        "question": question,
        "rubric_design_summary": design_summary,
        "method_overview": method_overview,
        "decision_summary": decision_summary,
        "reasoning_narrative": reasoning_narrative,
        "option_analyses": option_analyses,
    }


def _parse_dimension_scores(
    row: Dict[str, Any],
    dimensions: Sequence[Dict[str, Any]],
    evidence_id: str,
    assumptions: List[str],
) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    raw_scores = row.get("dimension_scores")
    use_map = isinstance(raw_scores, dict)

    for dim in dimensions:
        key = dim["key"]
        if use_map and key in raw_scores:
            value = raw_scores.get(key)
        elif key in row:
            value = row.get(key)
        else:
            value = 0.5
            assumptions.append(f"{evidence_id} missing score for '{key}'; defaulted to 0.5")
        scores[key] = _clip_01(value, default=0.5)
    return scores


def run_forecast(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _insufficient(
            payload=payload,
            blocking_reasons=["input payload must be a JSON object"],
            next_required_inputs=["JSON object payload"],
        )

    required_fields = ["question", "options", "resolution_rule", "evidence"]
    missing = [field for field in required_fields if field not in payload]
    if missing:
        return _insufficient(
            payload=payload,
            missing_fields=missing,
            blocking_reasons=["required input contract not satisfied"],
        )

    options_raw = payload.get("options")
    if not isinstance(options_raw, list) or len(options_raw) < 2:
        return _insufficient(
            payload=payload,
            missing_fields=["options"],
            blocking_reasons=["options must be a list with at least two candidates"],
        )
    options = [str(item) for item in options_raw]
    if len(set(options)) != len(options):
        return _insufficient(
            payload=payload,
            missing_fields=["options"],
            blocking_reasons=["options must be unique"],
        )

    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return _insufficient(
            payload=payload,
            missing_fields=["evidence"],
            blocking_reasons=["evidence must be a list"],
        )

    assumptions: List[str] = []
    forecast_time_raw = payload.get("forecast_time")
    if forecast_time_raw not in (None, "") and _parse_time(forecast_time_raw) is None:
        assumptions.append("forecast_time invalid and ignored")
    close_time_raw = payload.get("close_time")
    if close_time_raw not in (None, "") and _parse_time(close_time_raw) is None:
        assumptions.append("close_time invalid and ignored")

    dimensions = _normalize_dimensions(payload.get("rubric_dimensions"), assumptions)
    temperature = _normalize_temperature(payload.get("normalization_temperature"), assumptions)
    base_scores = _coerce_base_option_scores(payload.get("base_option_scores"), options, assumptions)

    option_set = set(options)
    dependency_seen: Dict[str, int] = {}
    evidence_ledger: List[Dict[str, Any]] = []
    conflict_notes: List[str] = []
    option_dimension_totals: Dict[str, Dict[str, float]] = {
        option: {dim["key"]: 0.0 for dim in dimensions} for option in options
    }

    for idx, row in enumerate(evidence):
        if not isinstance(row, dict):
            assumptions.append(f"evidence[{idx}] ignored: not an object")
            continue

        evidence_id = str(row.get("id", f"e{idx + 1}"))
        targets = [
            option
            for option in _extract_target_options(row.get("supports_option"))
            if option in option_set
        ]
        if not targets:
            assumptions.append(f"{evidence_id} ignored: supports_option missing or not in options")
            continue

        stance = str(row.get("stance", "for")).lower()
        if stance not in ("for", "against"):
            assumptions.append(f"{evidence_id} stance invalid; defaulted to 'for'")
            stance = "for"

        strength = str(row.get("strength", "medium")).lower()
        if strength not in STRENGTH_FACTOR_MAP:
            assumptions.append(f"{evidence_id} strength invalid; defaulted to 'medium'")
            strength = "medium"
        strength_factor = STRENGTH_FACTOR_MAP[strength]

        feature_values = _parse_dimension_scores(
            row=row,
            dimensions=dimensions,
            evidence_id=evidence_id,
            assumptions=assumptions,
        )
        quality = sum(
            float(dim["weight"]) * feature_values[dim["key"]] for dim in dimensions
        )

        dep_group_value = row.get("dependency_group")
        if isinstance(dep_group_value, str) and dep_group_value.strip():
            dep_group = dep_group_value.strip()
            seen = dependency_seen.get(dep_group, 0)
            dependency_penalty = 1.0 if seen == 0 else 0.5
            dependency_seen[dep_group] = seen + 1
        else:
            dep_group = None
            dependency_penalty = 1.0

        sign = 1.0 if stance == "for" else -1.0
        contribution_abs = quality * strength_factor * dependency_penalty
        signed_contribution = sign * contribution_abs
        dimension_contributions = {
            dim["key"]: float(dim["weight"])
            * feature_values[dim["key"]]
            * strength_factor
            * dependency_penalty
            * sign
            for dim in dimensions
        }

        for target in targets:
            for dim in dimensions:
                option_dimension_totals[target][dim["key"]] += dimension_contributions[
                    dim["key"]
                ]

        evidence_ledger.append(
            {
                "id": evidence_id,
                "targets": targets,
                "supports_option": targets if len(targets) > 1 else targets[0],
                "stance": stance,
                "strength": strength,
                "claim": str(row.get("claim", "")),
                "source": str(row.get("source", "")),
                "quality": quality,
                "strength_factor": strength_factor,
                "dependency_group": dep_group,
                "dependency_penalty": dependency_penalty,
                "dimension_scores": feature_values,
                "dimension_contributions": dimension_contributions,
                "contribution": signed_contribution,
                "abs_contribution": abs(signed_contribution),
            }
        )

    raw_scores, normalized = _compute_with_ledger(
        options=options,
        base_scores=base_scores,
        evidence_ledger=evidence_ledger,
        temperature=temperature,
    )

    ranked_options = _rank_options(options, normalized, raw_scores)
    top_option = ranked_options[0]

    sensitivity: List[Dict[str, Any]] = []
    if evidence_ledger:
        strongest = max(evidence_ledger, key=lambda row: float(row["abs_contribution"]))
        dropped_id = strongest["id"]
        _, normalized_dropped = _compute_with_ledger(
            options=options,
            base_scores=base_scores,
            evidence_ledger=evidence_ledger,
            temperature=temperature,
            exclude_evidence_ids=[dropped_id],
        )
        delta = normalized_dropped[top_option] - normalized[top_option]
        sensitivity.append(
            {
                "drop_evidence_id": dropped_id,
                "delta_top_score": round(delta, 6),
            }
        )

    monitoring_signals = payload.get("monitoring_signals")
    if not isinstance(monitoring_signals, list):
        monitoring_signals = []

    conflict_resolution_notes = conflict_notes + _detect_conflicts(evidence_ledger)
    multidim_analysis = _build_multidim_analysis(
        question=str(payload.get("question", "")),
        dimensions=dimensions,
        raw_scores=raw_scores,
        normalized=normalized,
        evidence_ledger=evidence_ledger,
        option_dimension_totals=option_dimension_totals,
        sensitivity=sensitivity,
        conflict_notes=conflict_resolution_notes,
    )
    reasoning_text = multidim_analysis["reasoning_narrative"]

    return {
        "status": "ok",
        "final_answer": top_option,
        "raw_option_scores": _format_score_rows(raw_scores, "score"),
        "normalized_scores": _format_score_rows(normalized, "score"),
        "rubric_dimensions": [
            {
                "key": dim["key"],
                "label": dim["label"],
                "weight": round(float(dim["weight"]), 6),
                "why_it_matters": dim.get("why_it_matters", ""),
            }
            for dim in dimensions
        ],
        "normalization_temperature": temperature,
        "evidence_ledger": [
            {
                "id": row["id"],
                "supports_option": row["supports_option"],
                "stance": row["stance"],
                "strength": row["strength"],
                "quality": round(float(row["quality"]), 6),
                "contribution": round(float(row["contribution"]), 6),
                "dimension_scores": {
                    key: round(float(value), 6)
                    for key, value in row["dimension_scores"].items()
                },
                "source": row["source"],
            }
            for row in evidence_ledger
        ],
        "conflict_resolution_notes": conflict_resolution_notes,
        "sensitivity": sensitivity,
        "reasoning_text": reasoning_text,
        "rubric_multidim_analysis": multidim_analysis,
        "monitoring_signals": monitoring_signals,
        "assumptions": assumptions,
        "engine": _engine_metadata(payload),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rubric forecasting runner")
    parser.add_argument("--input", help="Input JSON file path. Defaults to stdin.")
    parser.add_argument("--output", help="Output JSON file path. Defaults to stdout.")
    args = parser.parse_args()

    try:
        payload = _read_input(args.input)
    except Exception as exc:  # noqa: BLE001
        result = _insufficient(
            payload=None,
            blocking_reasons=[f"failed to read input JSON: {exc}"],
            next_required_inputs=["valid JSON payload"],
        )
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
                f.write("\n")
        else:
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1

    result = run_forecast(payload)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write("\n")
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
