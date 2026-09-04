"""LifeFun API adapter: discovery + typed payloads aligned with lifefun-frontend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from rubric_forecast.adapters.base import AdapterContext
from rubric_forecast.openclaw_adapter import OpenClawAdapter


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass
class AgentMintWithSigPayload:
    """EIP-712 mint params issued by backend (GET /v1/agents/{id}/mint)."""

    chain_id: int
    contract_address: str
    method: str
    to: str
    profile_hash: str
    uri: str
    nonce: str
    deadline: str
    expires_at: str
    signature: str


@dataclass
class ReasoningIntakeWithSigPayload:
    """UserActionRouter.intakeReasoning signed payload from memory_from_opinion."""

    chain_id: int
    contract_address: str
    method: str
    token_id: str
    source_opinion_id: str
    reasoning_hash: str
    opinion_hash: str
    new_memory_root: str
    nonce: str
    deadline: str
    expires_at: str
    signature: str


def agent_mint_from_dict(raw: Dict[str, Any]) -> AgentMintWithSigPayload:
    return AgentMintWithSigPayload(
        chain_id=int(raw.get("chain_id") or 0),
        contract_address=str(raw.get("contract_address") or ""),
        method=str(raw.get("method") or "mintWithSig"),
        to=str(raw.get("to") or ""),
        profile_hash=str(raw.get("profile_hash") or ""),
        uri=str(raw.get("uri") or ""),
        nonce=str(raw.get("nonce") or "0"),
        deadline=str(raw.get("deadline") or "0"),
        expires_at=str(raw.get("expires_at") or ""),
        signature=str(raw.get("signature") or ""),
    )


def reasoning_intake_from_dict(raw: Dict[str, Any]) -> ReasoningIntakeWithSigPayload:
    return ReasoningIntakeWithSigPayload(
        chain_id=int(raw.get("chain_id") or 0),
        contract_address=str(raw.get("contract_address") or ""),
        method=str(raw.get("method") or "intakeReasoning"),
        token_id=str(raw.get("token_id") or "0"),
        source_opinion_id=str(raw.get("source_opinion_id") or "0"),
        reasoning_hash=str(raw.get("reasoning_hash") or ""),
        opinion_hash=str(raw.get("opinion_hash") or ""),
        new_memory_root=str(raw.get("new_memory_root") or ""),
        nonce=str(raw.get("nonce") or "0"),
        deadline=str(raw.get("deadline") or "0"),
        expires_at=str(raw.get("expires_at") or ""),
        signature=str(raw.get("signature") or ""),
    )


def parse_mint_with_sig_from_response(api_body: Any) -> Optional[AgentMintWithSigPayload]:
    if not isinstance(api_body, dict):
        return None
    inner = api_body.get("mint_with_sig")
    if not isinstance(inner, dict):
        return None
    return agent_mint_from_dict(inner)


class LifeFunDAppAdapter:
    """Built-in discovery aligned with lifefun-frontend /api/autopilot/run discoverCandidates."""

    name = "lifefun"

    def discover_candidates(self, ctx: AdapterContext) -> List[Dict[str, Any]]:
        cfg = ctx.config
        api = ctx.api
        discovered: List[Dict[str, Any]] = []
        if not api.base_url:
            return discovered

        raw_preds = api.list_predictions(
            status="open",
            chain_id=cfg.chain_id,
            limit=cfg.autopilot_discovery_limit,
        )
        pred_items: List[Any]
        if isinstance(raw_preds, dict) and "items" in raw_preds:
            pred_items = list(raw_preds.get("items") or [])
        elif isinstance(raw_preds, list):
            pred_items = list(raw_preds)
        else:
            pred_items = []
        for it in pred_items:
            if not isinstance(it, dict):
                continue
            pid = it.get("id", "unknown")
            discovered.append(
                {
                    "meta": {
                        "id": f"prediction-{pid}",
                        "action_type": "predict_only",
                    },
                    "question": str(
                        it.get("topic_title") or it.get("title") or it.get("question") or f"prediction-{pid}"
                    ),
                    "options": ["yes", "no"],
                    "resolution_rule": str(
                        it.get("topic_description")
                        or it.get("description")
                        or "Resolve according to platform adjudication result."
                    ),
                    "evidence": [
                        {
                            "id": f"pred-{pid}",
                            "claim": "Discovered from open prediction list.",
                            "source": "lifefun.predictions",
                            "supports_option": "yes",
                            "stance": "for",
                            "strength": "medium",
                            "dimension_scores": {
                                "reliability": 0.6,
                                "mechanism_fit": 0.6,
                                "novelty": 0.5,
                                "timeliness": 0.7,
                            },
                        }
                    ],
                }
            )

        aid = cfg.autopilot_default_target_agent_id
        if aid is None:
            return discovered

        feeding = api.get_feeding(
            tab="recommended",
            chain_id=cfg.chain_id,
            limit=cfg.autopilot_discovery_limit,
        )
        rows = feeding if isinstance(feeding, list) else []

        for row in rows:
            if not isinstance(row, dict):
                continue
            opinion_id = _numeric(row.get("reasoning", {}).get("opinion_id") if isinstance(row.get("reasoning"), dict) else None)
            if opinion_id is None:
                opinion_id = _numeric(row.get("opinion_id"))
            if opinion_id is None:
                opinion_id = _numeric(row.get("id"))
            if opinion_id is None:
                continue
            oid = int(opinion_id)
            title = str(row.get("title") or "Should this reasoning be fed into the target agent?")
            rh = None
            if isinstance(row.get("reasoning"), dict):
                rh = row["reasoning"].get("reasoning_hash")

            if cfg.autopilot_enable_feed:
                discovered.append(
                    {
                        "meta": {
                            "id": f"feed-{oid}",
                            "action_type": "feed_reference",
                            "source_opinion_id": oid,
                            "target_agent_id": aid,
                        },
                        "question": title,
                        "options": ["yes", "no"],
                        "resolution_rule": "Choose yes when this reasoning is useful as agent memory reference.",
                        "evidence": [
                            {
                                "id": f"feed-{oid}",
                                "claim": "Recommended feed item discovered from platform feeding stream.",
                                "source": "lifefun.platform.feeding",
                                "supports_option": "yes",
                                "stance": "for",
                                "strength": "strong",
                                "dimension_scores": {
                                    "reliability": 0.7,
                                    "mechanism_fit": 0.78,
                                    "novelty": 0.6,
                                    "timeliness": 0.8,
                                },
                            }
                        ],
                    }
                )
            if cfg.autopilot_enable_adopt:
                discovered.append(
                    {
                        "meta": {
                            "id": f"adopt-{oid}",
                            "action_type": "adopt",
                            "agent_id": aid,
                            "opinion_id": oid,
                            "reasoning_hash": rh,
                            "submit_mode": "prepare_only",
                            "auto_onchain": cfg.autopilot_auto_onchain_adopt,
                        },
                        "question": title,
                        "options": ["yes", "no"],
                        "resolution_rule": "Choose yes when this reasoning should be adopted and prepared for onchain intake.",
                        "evidence": [
                            {
                                "id": f"adopt-{oid}",
                                "claim": "Reasoning item discovered from platform feed with adoption potential.",
                                "source": "lifefun.platform.feeding",
                                "supports_option": "yes",
                                "stance": "for",
                                "strength": "strong",
                                "dimension_scores": {
                                    "reliability": 0.72,
                                    "mechanism_fit": 0.8,
                                    "novelty": 0.58,
                                    "timeliness": 0.82,
                                },
                            }
                        ],
                    }
                )

        return discovered
