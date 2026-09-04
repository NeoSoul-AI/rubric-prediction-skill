#!/usr/bin/env python3
"""Generic topic enricher with pluggable type-aware evidence generation.

Current capabilities:
- Generic web research via Tavily (primary external search layer)
- Crypto-specific market enrichments (Binance / CoinGecko / Fear & Greed)
- Sports-specific search-derived matchup enrichments
- Geopolitics / politics-specific official-momentum enrichments
- Finance / tech lightweight type-aware enrichments on top of generic web evidence

Fail-open by design: source outages produce assumptions instead of hard pipeline failures.
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from evo_common import http_request_json

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
COINGECKO_SIMPLE_URL = "https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true&include_last_updated_at=true"
COINGECKO_MARKET_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&market_data=true&community_data=true&developer_data=false&sparkline=false"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
TAVILY_URL = "https://api.tavily.com/search"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) OpenClaw-EvoPipeline/1.0",
    "Accept": "application/json",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _http_json(url: str) -> Tuple[Optional[int], Any]:
    try:
        return http_request_json(url, "GET", headers=DEFAULT_HEADERS, timeout=20.0, retries=1)
    except Exception as exc:  # noqa: BLE001
        return None, {"error": str(exc)}


def _http_post_json(url: str, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Tuple[Optional[int], Any]:
    try:
        merged = dict(DEFAULT_HEADERS)
        if headers:
            merged.update(headers)
        return http_request_json(url, "POST", data=body, headers=merged, timeout=25.0, retries=1)
    except Exception as exc:  # noqa: BLE001
        return None, {"error": str(exc)}


def source_reliability(source: str) -> float:
    return {
        "binance": 0.92,
        "coingecko": 0.86,
        "fear_greed": 0.70,
        "tavily": 0.68,
        "topic_body": 0.62,
        "official_statement": 0.84,
        "sports_news": 0.66,
        "finance_news": 0.72,
        "tech_news": 0.68,
    }.get(source, 0.5)


def infer_asset(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]]) -> Dict[str, str]:
    text = _topic_text(topic, prediction).lower()
    if "ethereum" in text or re.search(r"\beth\b", text):
        return {"symbol": "ETH", "binance": "ETHUSDT", "coingecko": "ethereum"}
    return {"symbol": "BTC", "binance": "BTCUSDT", "coingecko": "bitcoin"}


def parse_thresholds(question: str) -> Dict[str, Any]:
    q = question.lower()
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)", question)]
    info: Dict[str, Any] = {"kind": "unknown", "values": nums}
    if "between" in q and len(nums) >= 2:
        info["kind"] = "between"
        info["low"] = min(nums[0], nums[1])
        info["high"] = max(nums[0], nums[1])
    elif ("above" in q or "reach" in q or "greater than" in q) and nums:
        info["kind"] = "above"
        info["target"] = nums[0]
    elif ("below" in q or "less than" in q or "dip to" in q) and nums:
        info["kind"] = "below"
        info["target"] = nums[0]
    return info


def _topic_text(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]]) -> str:
    return "\n".join(
        [
            str((prediction or {}).get("topic_title") or topic.get("title") or ""),
            str((prediction or {}).get("topic_description") or topic.get("description") or ""),
        ]
    ).strip()


def _topic_type(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]]) -> str:
    return str((prediction or {}).get("topic_type") or topic.get("type") or "generic").lower().strip() or "generic"


def _binary_options(topic: Dict[str, Any]) -> Tuple[str, str]:
    opts = topic.get("options") or []
    keys = [str(o.get("key")) for o in opts if isinstance(o, dict) and o.get("key")]
    if len(keys) >= 2:
        return keys[0], keys[1]
    return "yes", "no"


def tavily_search(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], topic_type: str) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    notes: List[str] = []
    diagnostics: Dict[str, Any] = {"provider": "tavily", "query": None, "results": []}
    if not key:
        notes.append("tavily key missing")
        return [], notes, diagnostics

    title = str((prediction or {}).get("topic_title") or topic.get("title") or "").strip()
    query = f"{title} {topic_type} latest developments evidence"
    diagnostics["query"] = query
    body = {
        "api_key": key,
        "query": query,
        "search_depth": "advanced",
        "max_results": 6,
        "include_answer": True,
        "include_raw_content": False,
        "topic": "news" if topic_type in {"geopolitics", "politics", "finance", "crypto"} else "general",
    }
    status, payload = _http_post_json(TAVILY_URL, body)
    diagnostics["status"] = status
    if status != 200 or not isinstance(payload, dict):
        notes.append(f"tavily search unavailable (status={status})")
        return [], notes, diagnostics

    results = payload.get("results") or []
    answer = str(payload.get("answer") or "").strip()
    diagnostics["answer"] = answer
    diagnostics["results"] = results[:6] if isinstance(results, list) else []
    if not isinstance(results, list) or not results:
        notes.append("tavily returned no results")
        return [], notes, diagnostics

    evidence: List[Dict[str, Any]] = []
    for idx, row in enumerate(results[:5]):
        if not isinstance(row, dict):
            continue
        snippet = str(row.get("content") or row.get("snippet") or "").strip()
        title_row = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        score = _safe_float(row.get("score")) or 0.55
        if not (snippet or title_row or url):
            continue
        evidence.append(
            {
                "id": f"tavily_{idx+1}",
                "claim": (snippet or title_row)[:500],
                "timestamp": _now_iso(),
                "source": url or "tavily",
                "supports_option": "yes",
                "stance": "for",
                "strength": "medium" if score < 0.72 else "strong",
                "dependency_group": "tavily_search",
                "source_type": "web_search",
                "source_reliability": min(0.8, max(0.55, score)),
                "search_title": title_row,
                "search_answer": answer[:300] if answer else "",
            }
        )
    return evidence, notes, diagnostics


def generic_dimension_scores(dimensions: List[Dict[str, Any]], confidence_hint: float = 0.58, negative: bool = False) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for idx, dim in enumerate(dimensions):
        key = str(dim.get("key") or f"d{idx}")
        base = confidence_hint - (0.03 * (idx % 3))
        out[key] = round(_clip01(1.0 - base if negative else base), 4)
    return out


def _text_has_any(text: str, patterns: List[str]) -> bool:
    lower = text.lower()
    return any(p in lower for p in patterns)


def build_generic_search_evidence(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], dimensions: List[Dict[str, Any]], topic_type: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    resolved_type = topic_type or _topic_type(topic, prediction)
    yes_opt, no_opt = _binary_options(topic)
    tavily_rows, notes, diagnostics = tavily_search(topic, prediction, resolved_type)
    evidence: List[Dict[str, Any]] = []
    for idx, row in enumerate(tavily_rows):
        score = _safe_float(row.get("source_reliability")) or 0.62
        score_map = generic_dimension_scores(dimensions, confidence_hint=min(0.72, max(0.48, score)))
        stance_target = yes_opt if idx % 2 == 0 else no_opt
        score_map = score_map if stance_target == yes_opt else {k: round(1.0 - v, 4) for k, v in score_map.items()}
        evidence.append(
            {
                **row,
                "supports_option": stance_target,
                "dimension_scores": score_map,
                "strength": "strong" if score >= 0.75 else row.get("strength", "medium"),
                "dependency_group": f"tavily_{resolved_type}",
            }
        )
    return evidence, notes, diagnostics


def _sports_score_maps(dimensions: List[Dict[str, Any]], positive: float, negative: bool = False) -> Dict[str, float]:
    defaults = {
        "team_strength_gap": positive,
        "format_upset_risk": max(0.35, positive - 0.10),
        "recent_form": max(0.40, positive - 0.05),
        "availability_execution": max(0.38, positive - 0.08),
    }
    out = {}
    for idx, dim in enumerate(dimensions):
        key = str(dim.get("key") or f"d{idx}")
        val = defaults.get(key, positive - 0.04 * (idx % 2))
        out[key] = round(_clip01(1.0 - val if negative else val), 4)
    return out


def build_sports_evidence(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], dimensions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    text = _topic_text(topic, prediction)
    yes_opt, no_opt = _binary_options(topic)
    generic_rows, notes, diagnostics = build_generic_search_evidence(topic, prediction, dimensions, topic_type="sports")
    evidence = list(generic_rows)

    score_text = text.lower()
    both_score = _text_has_any(score_text, ["both teams to score", "each score at least one goal"])
    winner_market = _text_has_any(score_text, ["winner", "win on", "game 4 winner"])
    title = str((prediction or {}).get("topic_title") or topic.get("title") or "")

    if both_score:
        evidence.append(
            {
                "id": "sports_market_shape",
                "claim": f"This is a both-teams-to-score market; attacking consistency and clean-sheet resistance matter more than outright team superiority for {title}.",
                "timestamp": _now_iso(),
                "source": "derived:sports_market_shape",
                "supports_option": yes_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": _sports_score_maps(dimensions, 0.67, negative=False),
                "dependency_group": "sports_market_structure",
                "source_type": "sports_structure",
                "source_reliability": source_reliability("sports_news"),
            }
        )
        evidence.append(
            {
                "id": "sports_market_shape_counter",
                "claim": "Counter-case: if either side is materially blunt in attack or one side can control shot volume, BTTS markets can fail despite an otherwise live match.",
                "timestamp": _now_iso(),
                "source": "derived:sports_market_shape",
                "supports_option": no_opt,
                "stance": "for",
                "strength": "weak",
                "dimension_scores": _sports_score_maps(dimensions, 0.61, negative=True),
                "dependency_group": "sports_market_structure",
                "source_type": "sports_structure",
                "source_reliability": 0.60,
            }
        )
    elif winner_market:
        evidence.append(
            {
                "id": "sports_winner_structure",
                "claim": f"This is a winner-style sports market; team strength gap and execution stability usually matter more than general event noise for {title}.",
                "timestamp": _now_iso(),
                "source": "derived:sports_winner_structure",
                "supports_option": yes_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": _sports_score_maps(dimensions, 0.64, negative=False),
                "dependency_group": "sports_market_structure",
                "source_type": "sports_structure",
                "source_reliability": 0.64,
            }
        )

    if generic_rows:
        top = generic_rows[0]
        evidence.append(
            {
                "id": "sports_search_consensus",
                "claim": f"Search-derived sports coverage points to a live pre-match information set around {title}; lineup and form updates should be weighted as late-moving signals.",
                "timestamp": _now_iso(),
                "source": top.get("source") or "tavily:sports",
                "supports_option": yes_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": _sports_score_maps(dimensions, 0.58, negative=False),
                "dependency_group": "sports_search_layer",
                "source_type": "sports_news",
                "source_reliability": 0.66,
            }
        )

    diagnostics["sports_flags"] = {"both_score": both_score, "winner_market": winner_market}
    return evidence, notes, diagnostics


def _geo_score_maps(dimensions: List[Dict[str, Any]], base: float, reverse: bool = False) -> Dict[str, float]:
    defaults = {
        "diplomatic_momentum": base,
        "official_willingness": max(0.35, base - 0.03),
        "time_window_feasibility": max(0.30, base - 0.06),
        "escalation_constraint": max(0.25, base - 0.09),
    }
    out = {}
    for idx, dim in enumerate(dimensions):
        key = str(dim.get("key") or f"d{idx}")
        val = defaults.get(key, base - 0.04 * (idx % 2))
        out[key] = round(_clip01(1.0 - val if reverse else val), 4)
    return out


def build_geopolitics_evidence(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], dimensions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    text = _topic_text(topic, prediction)
    yes_opt, no_opt = _binary_options(topic)
    generic_rows, notes, diagnostics = build_generic_search_evidence(topic, prediction, dimensions, topic_type="geopolitics")
    evidence = list(generic_rows)
    lower = text.lower()

    if _text_has_any(lower, ["diplomatic meeting", "negotiation", "talks"]):
        evidence.append(
            {
                "id": "geo_meeting_template",
                "claim": "Official diplomatic events require both public momentum and authorized attendance; even strong media chatter can fail the resolution rule if the meeting never formally occurs.",
                "timestamp": _now_iso(),
                "source": "derived:geo_meeting_template",
                "supports_option": yes_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": _geo_score_maps(dimensions, 0.66, reverse=False),
                "dependency_group": "geo_structure",
                "source_type": "official_statement",
                "source_reliability": source_reliability("official_statement"),
            }
        )
        evidence.append(
            {
                "id": "geo_meeting_counter",
                "claim": "Counter-case: these markets are often decided by late procedural failure, denial, or attendance uncertainty even when diplomatic momentum briefly improves.",
                "timestamp": _now_iso(),
                "source": "derived:geo_meeting_template",
                "supports_option": no_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": _geo_score_maps(dimensions, 0.62, reverse=True),
                "dependency_group": "geo_structure",
                "source_type": "official_statement",
                "source_reliability": 0.76,
            }
        )

    if _text_has_any(lower, ["permanent peace deal", "peace deal", "ceasefire"]):
        evidence.append(
            {
                "id": "geo_deal_structure",
                "claim": "Permanent settlement markets are structurally harder than meeting markets because they require not just contact but an explicit durable agreement before the deadline.",
                "timestamp": _now_iso(),
                "source": "derived:geo_deal_structure",
                "supports_option": no_opt,
                "stance": "for",
                "strength": "strong",
                "dimension_scores": _geo_score_maps(dimensions, 0.70, reverse=True),
                "dependency_group": "geo_deal_structure",
                "source_type": "official_statement",
                "source_reliability": 0.80,
            }
        )

    if _text_has_any(lower, ["traffic returns to normal", "strait of hormuz", "transit calls"]):
        evidence.append(
            {
                "id": "geo_flow_metric",
                "claim": "Traffic-normalization markets are metric-driven; the decisive question is whether observed operational throughput can recover fast enough inside the stated observation window.",
                "timestamp": _now_iso(),
                "source": "derived:geo_flow_metric",
                "supports_option": yes_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": _geo_score_maps(dimensions, 0.58, reverse=False),
                "dependency_group": "geo_metric_structure",
                "source_type": "official_statement",
                "source_reliability": 0.72,
            }
        )

    diagnostics["geo_flags"] = {
        "meeting": _text_has_any(lower, ["diplomatic meeting", "talks"]),
        "peace_deal": _text_has_any(lower, ["peace deal", "ceasefire"]),
        "traffic_metric": _text_has_any(lower, ["strait of hormuz", "transit calls"]),
    }
    return evidence, notes, diagnostics


def build_finance_evidence(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], dimensions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    yes_opt, _ = _binary_options(topic)
    generic_rows, notes, diagnostics = build_generic_search_evidence(topic, prediction, dimensions, topic_type="finance")
    evidence = list(generic_rows)
    evidence.append(
        {
            "id": "finance_window_structure",
            "claim": "Finance-event markets should emphasize policy timing, explicit disclosed metrics, and whether the resolution rule depends on a reported figure versus a narrative judgment.",
            "timestamp": _now_iso(),
            "source": "derived:finance_structure",
            "supports_option": yes_opt,
            "stance": "for",
            "strength": "medium",
            "dimension_scores": generic_dimension_scores(dimensions, 0.62),
            "dependency_group": "finance_structure",
            "source_type": "finance_news",
            "source_reliability": source_reliability("finance_news"),
        }
    )
    return evidence, notes, diagnostics


def build_tech_evidence(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], dimensions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    yes_opt, _ = _binary_options(topic)
    generic_rows, notes, diagnostics = build_generic_search_evidence(topic, prediction, dimensions, topic_type="tech")
    evidence = list(generic_rows)
    evidence.append(
        {
            "id": "tech_delivery_structure",
            "claim": "Tech/product markets usually hinge on whether an official ship event, release note, or public launch artifact exists by the deadline, not merely on rumors or roadmap intent.",
            "timestamp": _now_iso(),
            "source": "derived:tech_structure",
            "supports_option": yes_opt,
            "stance": "for",
            "strength": "medium",
            "dimension_scores": generic_dimension_scores(dimensions, 0.60),
            "dependency_group": "tech_structure",
            "source_type": "tech_news",
            "source_reliability": source_reliability("tech_news"),
        }
    )
    return evidence, notes, diagnostics


def build_crypto_evidence(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], dimensions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    question = str((prediction or {}).get("topic_title") or topic.get("title") or "")
    asset = infer_asset(topic, prediction)
    threshold = parse_thresholds(question)
    notes: List[str] = []
    evidence: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {"asset": asset, "threshold": threshold, "fetched_at": _now_iso(), "sources": {}}
    yes_opt, no_opt = _binary_options(topic)

    binance_price = None
    change_24h = None
    st_bin, bin_payload = _http_json(BINANCE_TICKER_URL.format(symbol=asset["binance"]))
    diagnostics["sources"]["binance"] = {"status": st_bin}
    if st_bin == 200 and isinstance(bin_payload, dict):
        binance_price = _safe_float(bin_payload.get("lastPrice"))
        change_24h = _safe_float(bin_payload.get("priceChangePercent"))
    else:
        notes.append("binance ticker unavailable during enrichment")

    st_cg, cg_payload = _http_json(COINGECKO_SIMPLE_URL.format(coin_id=asset["coingecko"]))
    diagnostics["sources"]["coingecko_simple"] = {"status": st_cg}
    cg_price = None
    cg_change = None
    if st_cg == 200 and isinstance(cg_payload, dict):
        row = cg_payload.get(asset["coingecko"]) or {}
        cg_price = _safe_float(row.get("usd"))
        cg_change = _safe_float(row.get("usd_24h_change"))
    else:
        notes.append("coingecko simple price unavailable during enrichment")

    st_cgm, cgm_payload = _http_json(COINGECKO_MARKET_URL.format(coin_id=asset["coingecko"]))
    diagnostics["sources"]["coingecko_market"] = {"status": st_cgm}
    ath_drawdown = None
    community_score = None
    if st_cgm == 200 and isinstance(cgm_payload, dict):
        md = cgm_payload.get("market_data") or {}
        ath_drawdown = _safe_float((md.get("ath_change_percentage") or {}).get("usd"))
        comm = cgm_payload.get("community_data") or {}
        twitter = _safe_float(comm.get("twitter_followers")) or 0.0
        reddit = _safe_float(comm.get("reddit_subscribers")) or 0.0
        community_score = min(1.0, math.log10(max(1.0, twitter + reddit)) / 7.0)
    else:
        notes.append("coingecko market data unavailable during enrichment")

    st_fg, fg_payload = _http_json(FEAR_GREED_URL)
    diagnostics["sources"]["fear_greed"] = {"status": st_fg}
    fear_greed = None
    if st_fg == 200 and isinstance(fg_payload, dict):
        data = fg_payload.get("data") or []
        if data and isinstance(data[0], dict):
            fear_greed = _safe_float(data[0].get("value"))
    else:
        notes.append("fear and greed index unavailable during enrichment")

    generic_rows, generic_notes, generic_diag = build_generic_search_evidence(topic, prediction, dimensions, topic_type="crypto")
    notes.extend(generic_notes)
    diagnostics["sources"]["tavily"] = generic_diag

    spot = binance_price or cg_price
    if spot is not None:
        distance_score_yes = 0.5
        threshold_precision_yes = 0.5
        claim_core = ""
        if threshold.get("kind") == "above" and threshold.get("target"):
            target = float(threshold["target"])
            move_pct = (target - spot) / spot
            distance_score_yes = _clip01(1.0 - max(0.0, move_pct) / 0.18)
            threshold_precision_yes = _clip01(0.85 - abs(move_pct) / 0.25)
            claim_core = f"Spot {spot:.2f} vs upside target {target:.0f}; required move {move_pct*100:.2f}%"
        elif threshold.get("kind") == "below" and threshold.get("target"):
            target = float(threshold["target"])
            move_pct = (spot - target) / spot
            distance_score_yes = _clip01(1.0 - max(0.0, move_pct) / 0.18)
            threshold_precision_yes = _clip01(0.85 - abs(move_pct) / 0.25)
            claim_core = f"Spot {spot:.2f} vs downside target {target:.0f}; required drawdown {move_pct*100:.2f}%"
        elif threshold.get("kind") == "between" and threshold.get("low") and threshold.get("high"):
            low = float(threshold["low"])
            high = float(threshold["high"])
            mid = (low + high) / 2.0
            band = max(1.0, high - low)
            dist_mid = abs(spot - mid) / max(spot, 1.0)
            distance_score_yes = _clip01(1.0 - dist_mid / 0.16)
            threshold_precision_yes = _clip01(1.0 - (band / max(mid, 1.0)) / 0.08)
            claim_core = f"Spot {spot:.2f} vs target band {low:.0f}-{high:.0f}; midpoint gap {(dist_mid*100):.2f}%"

        vol_score = 0.5
        regime_score = 0.5
        ref_change = change_24h if change_24h is not None else cg_change
        if ref_change is not None:
            vol_score = _clip01(abs(ref_change) / 12.0)
            regime_score = _clip01((ref_change + 10.0) / 20.0)
        if fear_greed is not None:
            regime_score = _clip01((regime_score * 0.6) + ((fear_greed / 100.0) * 0.4))

        base_yes = {
            "distance_to_target": round(distance_score_yes, 4),
            "short_term_volatility": round(vol_score, 4),
            "market_regime": round(regime_score, 4),
            "threshold_precision": round(threshold_precision_yes, 4),
        }
        base_no = {k: round(1.0 - v, 4) for k, v in base_yes.items()}

        evidence.append({
            "id": "mkt_spot_distance",
            "claim": claim_core,
            "timestamp": _now_iso(),
            "source": f"binance:{asset['binance']}",
            "supports_option": yes_opt,
            "stance": "for",
            "strength": "strong" if distance_score_yes >= 0.7 else "medium",
            "dimension_scores": base_yes,
            "dependency_group": "market_price",
            "source_type": "market_data",
            "source_reliability": source_reliability("binance"),
        })
        evidence.append({
            "id": "mkt_spot_counter",
            "claim": f"Counter-case from current spot distance and threshold difficulty for {question}",
            "timestamp": _now_iso(),
            "source": f"coingecko:{asset['coingecko']}",
            "supports_option": no_opt,
            "stance": "for",
            "strength": "medium",
            "dimension_scores": base_no,
            "dependency_group": "market_price",
            "source_type": "market_data",
            "source_reliability": source_reliability("coingecko"),
        })
        if fear_greed is not None:
            fg_yes = dict(base_yes)
            fg_yes["market_regime"] = round(_clip01(fear_greed / 100.0), 4)
            evidence.append({
                "id": "macro_fear_greed",
                "claim": f"Fear & Greed index at {fear_greed:.0f} gives a cross-market risk appetite read for crypto.",
                "timestamp": _now_iso(),
                "source": "alternative.me/fng",
                "supports_option": yes_opt if fg_yes["market_regime"] >= 0.55 else no_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": fg_yes if fg_yes["market_regime"] >= 0.55 else {k: round(1-v,4) for k,v in fg_yes.items()},
                "dependency_group": "macro_sentiment",
                "source_type": "macro_sentiment",
                "source_reliability": source_reliability("fear_greed"),
            })
        if ath_drawdown is not None or community_score is not None:
            chainish = dict(base_yes)
            if ath_drawdown is not None:
                chainish["distance_to_target"] = round(_clip01(1.0 - min(abs(ath_drawdown), 80.0) / 80.0), 4)
            if community_score is not None:
                chainish["market_regime"] = round((chainish["market_regime"] * 0.7) + (community_score * 0.3), 4)
            evidence.append({
                "id": "market_structure",
                "claim": "Broader crypto market structure from CoinGecko market/community fields provides a cross-check on conviction and participation.",
                "timestamp": _now_iso(),
                "source": f"coingecko:{asset['coingecko']}:market_data",
                "supports_option": yes_opt if chainish["distance_to_target"] >= 0.5 else no_opt,
                "stance": "for",
                "strength": "medium",
                "dimension_scores": chainish if chainish["distance_to_target"] >= 0.5 else {k: round(1-v,4) for k,v in chainish.items()},
                "dependency_group": "market_structure",
                "source_type": "market_structure",
                "source_reliability": source_reliability("coingecko"),
            })
    else:
        notes.append("no usable spot price from Binance or CoinGecko")

    evidence.extend(generic_rows)
    return evidence, notes, diagnostics


def build_signal_evidence(topic: Dict[str, Any], prediction: Optional[Dict[str, Any]], dimensions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any], List[str]]:
    topic_type = _topic_type(topic, prediction)
    notes: List[str] = []
    diagnostics: Dict[str, Any] = {"topic_type": topic_type, "fetched_at": _now_iso(), "router": "generic+type-aware"}
    monitoring: List[str] = ["fresh external search evidence", "source corroboration", "resolution-rule edge cases"]

    if topic_type == "crypto":
        evidence, extra_notes, extra_diag = build_crypto_evidence(topic, prediction, dimensions)
        notes.extend(extra_notes)
        diagnostics["type_enricher"] = extra_diag
        monitoring.extend(["spot price distance to target", "24h volatility regime", "fear and greed trend"])
        return evidence, notes, diagnostics, monitoring

    if topic_type == "sports":
        evidence, extra_notes, extra_diag = build_sports_evidence(topic, prediction, dimensions)
        notes.extend(extra_notes)
        diagnostics["type_enricher"] = extra_diag
        monitoring.extend(["recent team form", "lineup/injury updates", "odds movement"])
        return evidence, notes, diagnostics, monitoring

    if topic_type in {"geopolitics", "politics"}:
        evidence, extra_notes, extra_diag = build_geopolitics_evidence(topic, prediction, dimensions)
        notes.extend(extra_notes)
        diagnostics["type_enricher"] = extra_diag
        monitoring.extend(["official statements", "timeline developments", "escalation/de-escalation signals"])
        return evidence, notes, diagnostics, monitoring

    if topic_type == "finance":
        evidence, extra_notes, extra_diag = build_finance_evidence(topic, prediction, dimensions)
        notes.extend(extra_notes)
        diagnostics["type_enricher"] = extra_diag
        monitoring.extend(["reported metrics", "policy/event timing", "guidance or disclosure updates"])
        return evidence, notes, diagnostics, monitoring

    if topic_type == "tech":
        evidence, extra_notes, extra_diag = build_tech_evidence(topic, prediction, dimensions)
        notes.extend(extra_notes)
        diagnostics["type_enricher"] = extra_diag
        monitoring.extend(["official release artifacts", "roadmap or launch updates", "deadline slippage signals"])
        return evidence, notes, diagnostics, monitoring

    evidence, extra_notes, extra_diag = build_generic_search_evidence(topic, prediction, dimensions, topic_type=topic_type)
    notes.extend(extra_notes)
    diagnostics["type_enricher"] = extra_diag
    monitoring.append("new factual developments tied to the event window")
    return evidence, notes, diagnostics, monitoring
