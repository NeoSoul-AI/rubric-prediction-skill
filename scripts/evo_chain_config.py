#!/usr/bin/env python3
"""Helpers for resolving per-chain runtime config in serial multi-chain mode."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


def configured_chains(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    chains = ((cfg.get("openclaw") or {}).get("chains") or [])
    out = [c for c in chains if isinstance(c, dict) and c.get("chain_id") is not None]
    if out:
        return out
    cid = cfg.get("chain_id")
    return [{"chain_id": cid, "name": f"chain-{cid}"}] if cid is not None else []


def config_for_chain(cfg: Dict[str, Any], chain_id: int) -> Dict[str, Any]:
    out = deepcopy(cfg)
    out["chain_id"] = int(chain_id)
    chain_rows = configured_chains(cfg)
    chosen = None
    for row in chain_rows:
        try:
            if int(row.get("chain_id")) == int(chain_id):
                chosen = deepcopy(row)
                break
        except (TypeError, ValueError):
            continue
    oc = dict(out.get("openclaw") or {})
    if chosen is not None:
        oc["active_chain"] = chosen
    out["openclaw"] = oc
    return out
