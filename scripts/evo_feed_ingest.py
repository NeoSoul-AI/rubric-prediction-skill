#!/usr/bin/env python3
"""Fetch EvoEvo feed + topic detail, map to rubric-forecast input JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from evo_chain_config import config_for_chain
from evo_common import http_request_json, load_config, skill_root
from evo_signal_enrich import build_signal_evidence


SPORTS_DIMENSIONS = [
    {
        "key": "team_strength_gap",
        "label": "Team strength gap",
        "weight": 0.34,
        "why_it_matters": "In sports outcome markets, the overall strength and stability gap between the two sides is often the most direct first-order variable.",
    },
    {
        "key": "format_upset_risk",
        "label": "Format upset risk",
        "weight": 0.22,
        "why_it_matters": "Match format changes upset probability, and shorter formats amplify variance in particular.",
    },
    {
        "key": "recent_form",
        "label": "Recent form",
        "weight": 0.24,
        "why_it_matters": "Short-term form, adaptation, and live execution often change how much paper strength actually converts into results.",
    },
    {
        "key": "availability_execution",
        "label": "Availability and execution stability",
        "weight": 0.20,
        "why_it_matters": "Roster completeness, error rate, and execution stability directly affect whether the favored side converts its edge.",
    },
]

CRYPTO_RANGE_DIMENSIONS = [
    {
        "key": "distance_to_target",
        "label": "Distance to target",
        "weight": 0.36,
        "why_it_matters": "The farther price is from the target range, the larger the move required to reach it within a short time window.",
    },
    {
        "key": "short_term_volatility",
        "label": "Short-term volatility conditions",
        "weight": 0.24,
        "why_it_matters": "Sufficient volatility is what creates a realistic path for price to reach the specified range.",
    },
    {
        "key": "market_regime",
        "label": "Market regime",
        "weight": 0.20,
        "why_it_matters": "Trend, risk appetite, and macro sentiment shape whether continuation or reversal is more likely.",
    },
    {
        "key": "threshold_precision",
        "label": "Threshold precision difficulty",
        "weight": 0.20,
        "why_it_matters": "Range questions are not just directional calls; they ask whether price lands in a precise bracket at a specific time.",
    },
]

GEOPOLITICS_MEETING_DIMENSIONS = [
    {
        "key": "diplomatic_momentum",
        "label": "Diplomatic momentum",
        "weight": 0.32,
        "why_it_matters": "Clear contact, mediation, or public diplomatic movement is often the direct prerequisite for a formal meeting to happen.",
    },
    {
        "key": "official_willingness",
        "label": "Official willingness",
        "weight": 0.28,
        "why_it_matters": "Whether both sides are willing to engage through formally authorized representatives determines whether the event can satisfy the resolution rule.",
    },
    {
        "key": "time_window_feasibility",
        "label": "Time-window feasibility",
        "weight": 0.22,
        "why_it_matters": "The closer the deadline is, the easier it is for improving conditions to arrive too late for a formal meeting.",
    },
    {
        "key": "escalation_constraint",
        "label": "Escalation constraint",
        "weight": 0.18,
        "why_it_matters": "Escalation or worsening conditions can directly compress the room for an official diplomatic meeting.",
    },
]

GENERIC_DIMENSIONS = [
    {
        "key": "reliability",
        "label": "Reliability",
        "weight": 0.35,
        "why_it_matters": "Measures whether the evidence source is credible and verifiable.",
    },
    {
        "key": "mechanism_fit",
        "label": "Mechanism fit",
        "weight": 0.25,
        "why_it_matters": "Measures whether the evidence has a clear causal link to the outcome.",
    },
    {
        "key": "novelty",
        "label": "Novelty",
        "weight": 0.20,
        "why_it_matters": "Measures whether the information adds new decision value.",
    },
    {
        "key": "timeliness",
        "label": "Timeliness",
        "weight": 0.20,
        "why_it_matters": "Measures whether the information is close enough to the current decision window.",
    },
]


def infer_rubric_dimensions(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    topic_type = str((prediction or {}).get("topic_type") or topic.get("type") or "").lower()
    title = str((prediction or {}).get("topic_title") or topic.get("title") or "").lower()
    desc = str((prediction or {}).get("topic_description") or topic.get("description") or "").lower()
    text = f"{title}\n{desc}"

    if topic_type == "sports":
        return [dict(row) for row in SPORTS_DIMENSIONS]

    if topic_type == "crypto":
        if "price of bitcoin" in text or "btc" in text or "binance" in text:
            return [dict(row) for row in CRYPTO_RANGE_DIMENSIONS]

    if topic_type == "geopolitics":
        if "diplomatic meeting" in text or ("united states" in text and "iran" in text):
            return [dict(row) for row in GEOPOLITICS_MEETING_DIMENSIONS]

    return [dict(row) for row in GENERIC_DIMENSIONS]


def default_dimension_scores(dimensions: List[Dict[str, Any]], favored: bool) -> Dict[str, float]:
    keys = [str(dim.get("key") or "") for dim in dimensions]
    if set(keys) == {"team_strength_gap", "format_upset_risk", "recent_form", "availability_execution"}:
        return {
            "team_strength_gap": 0.70 if favored else 0.44,
            "format_upset_risk": 0.58 if favored else 0.46,
            "recent_form": 0.64 if favored else 0.45,
            "availability_execution": 0.62 if favored else 0.47,
        }
    if set(keys) == {"distance_to_target", "short_term_volatility", "market_regime", "threshold_precision"}:
        return {
            "distance_to_target": 0.66 if favored else 0.40,
            "short_term_volatility": 0.57 if favored else 0.47,
            "market_regime": 0.54 if favored else 0.46,
            "threshold_precision": 0.60 if favored else 0.43,
        }
    if set(keys) == {"diplomatic_momentum", "official_willingness", "time_window_feasibility", "escalation_constraint"}:
        return {
            "diplomatic_momentum": 0.62 if favored else 0.42,
            "official_willingness": 0.58 if favored else 0.43,
            "time_window_feasibility": 0.55 if favored else 0.44,
            "escalation_constraint": 0.48 if favored else 0.56,
        }
    return {
        "reliability": 0.55 if favored else 0.45,
        "mechanism_fit": 0.52 if favored else 0.45,
        "novelty": 0.48 if favored else 0.45,
        "timeliness": 0.54 if favored else 0.45,
    }


def fetch_feed(cfg: Dict[str, Any]) -> Dict[str, Any]:
    fb = cfg["feed"]
    base = str(cfg["api_base"]).rstrip("/")
    q = (
        f"{fb['path']}?limit={int(fb['limit'])}"
        f"&status={fb['status']}"
        f"&min_settled_predictions={int(fb['min_settled_predictions'])}"
        f"&type={fb['type']}"
        f"&chain_id={int(cfg['chain_id'])}"
    )
    url = base + q
    status, payload = http_request_json(url, "GET")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"feed failed HTTP {status}: {payload}")
    return payload


def fetch_topic(cfg: Dict[str, Any], topic_id: int) -> Dict[str, Any]:
    base = str(cfg["api_base"]).rstrip("/")
    url = f"{base}/v1/topics/{topic_id}"
    status, payload = http_request_json(url, "GET")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"topic {topic_id} failed HTTP {status}: {payload}")
    return payload


def fetch_prediction_for_topic(cfg: Dict[str, Any], topic_id: int) -> Optional[Dict[str, Any]]:
    base = str(cfg["api_base"]).rstrip("/")
    url = f"{base}/v1/predictions?topic_id={topic_id}"
    status, payload = http_request_json(url, "GET")
    if status != 200 or not isinstance(payload, dict):
        return None
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    # Prefer same chain_id
    cid = int(cfg["chain_id"])
    for row in items:
        if isinstance(row, dict) and int(row.get("chain_id") or 0) == cid:
            return row
    return items[0] if isinstance(items[0], dict) else None


def topic_allowed(topic: Dict[str, Any], allowlist: List[str]) -> bool:
    st = str(topic.get("status") or "").lower()
    return st in [a.lower() for a in allowlist]


def topic_to_rubric_input(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts_raw = topic.get("options") or []
    if not isinstance(opts_raw, list) or len(opts_raw) < 2:
        raise ValueError("topic.options must have at least 2 entries")
    options = [str(o["key"]) for o in opts_raw if isinstance(o, dict) and o.get("key")]

    base_scores: Dict[str, float] = {}
    counts = topic.get("option_counts") or topic.get("human_option_counts") or []
    if isinstance(counts, list):
        for row in counts:
            if not isinstance(row, dict):
                continue
            k = row.get("option_key")
            if k not in options:
                continue
            ratio = float(row.get("ratio") or 0) / 100.0
            base_scores[str(k)] = (ratio - 0.5) * 0.4

    desc = str(topic.get("description") or "").strip()
    title = str(topic.get("title") or "").strip()
    resolution = desc[:2000] if desc else title
    src = str(topic.get("source_url") or "").strip()
    tid = topic.get("id")

    dimensions = infer_rubric_dimensions(topic, prediction)

    evidence: List[Dict[str, Any]] = [
        {
            "id": "e_topic_body",
            "claim": (desc[:600] if desc else title)[:600],
            "source": src or f"topic:{tid}",
            "supports_option": options[0],
            "stance": "for",
            "strength": "medium",
            "dimension_scores": default_dimension_scores(dimensions, favored=True),
            "dependency_group": "topic_root",
            "source_type": "topic_body",
            "source_reliability": 0.62,
        }
    ]
    if len(options) > 1:
        evidence.append(
            {
                "id": "e_alt",
                "claim": "Counterfactual / alternative outcome channel",
                "source": src or f"topic:{tid}",
                "supports_option": options[1],
                "stance": "for",
                "strength": "weak",
                "dimension_scores": default_dimension_scores(dimensions, favored=False),
                "dependency_group": "topic_root",
                "source_type": "topic_body",
                "source_reliability": 0.48,
            }
        )

    enrichment_notes: List[str] = []
    external_signals: Dict[str, Any] = {}
    monitoring_signals: List[str] = []
    enriched_evidence, enrichment_notes, external_signals, monitoring_signals = build_signal_evidence(topic, prediction, dimensions)
    if enriched_evidence:
        evidence.extend(enriched_evidence)

    return {
        "question": title,
        "options": options,
        "resolution_rule": resolution,
        "close_time": topic.get("close_at"),
        "base_option_scores": base_scores,
        "rubric_dimensions": dimensions,
        "evidence": evidence,
        "monitoring_signals": monitoring_signals,
        "external_signals": external_signals,
        "assumptions": enrichment_notes,
    }


def pick_topic_id_from_feed(
    feed: Dict[str, Any], allowlist: List[str], skip_ids: Optional[set] = None
) -> Optional[int]:
    skip_ids = skip_ids or set()
    items = feed.get("items") or []
    if not isinstance(items, list):
        return None
    for row in items:
        if not isinstance(row, dict) or row.get("type") != "topic":
            continue
        tid = row.get("topic_id") or row.get("id")
        if tid is None:
            continue
        tid_int = int(tid)
        if tid_int in skip_ids:
            continue
        st = str(row.get("status") or "").lower()
        if st in [a.lower() for a in allowlist]:
            return tid_int
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=skill_root() / "config" / "evo_config.json")
    parser.add_argument("--topic-id", type=int, default=0)
    parser.add_argument("--chain-id", type=int, default=0, help="Override config chain_id for serial multi-chain runs")
    parser.add_argument("--out", type=Path, help="Write rubric input JSON")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.chain_id:
        cfg = config_for_chain(cfg, args.chain_id)
    allow = list(cfg["feed"].get("topic_status_allowlist") or ["open", "locked"])

    if args.topic_id:
        topic = fetch_topic(cfg, args.topic_id)
    else:
        feed = fetch_feed(cfg)
        tid = pick_topic_id_from_feed(feed, allow)
        if tid is None:
            print("No eligible topic in feed; pass --topic-id", file=sys.stderr)
            sys.exit(2)
        topic = fetch_topic(cfg, tid)

    prediction = fetch_prediction_for_topic(cfg, int(topic["id"]))
    rubric_in = topic_to_rubric_input(topic, prediction)
    out = {
        "topic": {"id": topic.get("id"), "status": topic.get("status"), "chain_id": cfg["chain_id"], "chain_name": ((cfg.get("openclaw") or {}).get("active_chain") or {}).get("name")},
        "prediction": prediction,
        "rubric_input": rubric_in,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
