#!/usr/bin/env python3
"""Multi-wallet submit: artifact, HTTP, hook, or OpenClaw opinions API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from evo_auth import load_wallets, login_wallet, wallet_account
from evo_chain_config import config_for_chain
from evo_common import ensure_dir, http_request_json, load_config, skill_root

SUBMIT_RETRIES = 2


def load_cursor(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "submitted": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cursor(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def already_submitted(cursor: Dict[str, Any], topic_id: int, address: str) -> bool:
    addr_l = address.lower()
    for row in cursor.get("submitted") or []:
        if not isinstance(row, dict):
            continue
        if int(row.get("topic_id") or 0) != topic_id:
            continue
        if str(row.get("address") or "").lower() == addr_l:
            return True
    return False


def openclaw_cursor_path(cfg: Dict[str, Any]) -> Path:
    """Resolve OpenClaw cursor file path (relative paths from skill root)."""
    oc = cfg.get("openclaw") or {}
    rel = str(oc.get("openclaw_cursor_file") or "state/openclaw_cursor.json")
    p = Path(rel).expanduser()
    if not p.is_absolute():
        p = skill_root() / p
    return p


def openclaw_topic_complete_for_wallets(
    cursor: Dict[str, Any], prediction_id: int, wallet_addresses: List[str]
) -> bool:
    """True when every listed address has an openclaw submission for this prediction_id."""
    if not wallet_addresses:
        return False
    needed = {a.lower() for a in wallet_addresses}
    done: Set[str] = set()
    for row in cursor.get("submitted") or []:
        if not isinstance(row, dict):
            continue
        if int(row.get("prediction_id") or 0) != int(prediction_id):
            continue
        done.add(str(row.get("address") or "").lower())
    return needed <= done


def already_submitted_openclaw(
    cursor: Dict[str, Any], prediction_id: int, address: str
) -> bool:
    addr_l = address.lower()
    for row in cursor.get("submitted") or []:
        if not isinstance(row, dict):
            continue
        if int(row.get("prediction_id") or 0) != int(prediction_id):
            continue
        if str(row.get("address") or "").lower() == addr_l:
            return True
    return False


def build_http_body(bundle: Dict[str, Any], wallet: Dict[str, Any]) -> Dict[str, Any]:
    sp = dict(bundle.get("submit_payload_suggested") or {})
    sp["wallet_label"] = wallet.get("label")
    sp["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return sp


def candidate_rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("items", "candidates", "predictions", "data"):
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    if payload.get("prediction_id") is not None:
        return [payload]
    return []


def prediction_id_from_candidate_row(row: Dict[str, Any]) -> Optional[int]:
    for k in ("prediction_id", "id", "api_prediction_id"):
        v = row.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def option_keys_from_candidate_row(row: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    opts = row.get("options")
    if isinstance(opts, list):
        for o in opts:
            if isinstance(o, dict) and o.get("option_key") is not None:
                keys.append(str(o["option_key"]))
    return keys


def fetch_openclaw_candidates(
    api_base: str, token: str, topic_id: int
) -> Tuple[int, Any]:
    base = api_base.rstrip("/")
    url = f"{base}/v1/openclaw/predictions/candidates?topic_id={int(topic_id)}"
    headers = {"Authorization": f"Bearer {token}"}
    return http_request_json(
        url,
        "GET",
        None,
        headers=headers,
        retries=SUBMIT_RETRIES,
        retry_backoff_seconds=1.5,
    )


def extract_agent_id(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    for key in ("agent_id", "id"):
        v = payload.get(key)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("agent_id", "id"):
            v = data.get(key)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
    return None


def create_openclaw_agent(api_base: str, token: str, domain_focus: str = "geopolitics") -> int:
    base = api_base.rstrip("/")
    url = f"{base}/v1/agents"
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "name": "OpenClaw Bot",
        "agent_type": "openclaw",
        "llm_model": "openclaw",
        "style": "analyst",
        "domain_focus": [domain_focus],
        "risk_preference": "balanced",
        "prompt": "Auto-created during integration test",
    }
    status, payload = http_request_json(
        url,
        "POST",
        body,
        headers=headers,
        retries=SUBMIT_RETRIES,
        retry_backoff_seconds=1.5,
    )
    if status not in (200, 201):
        raise RuntimeError(f"agents POST HTTP {status}: {payload}")
    agent_id = extract_agent_id(payload)
    if agent_id is None:
        raise RuntimeError(f"agents POST missing agent_id: {payload}")
    return agent_id


def fetch_openclaw_me(api_base: str, token: str, auto_create: bool = True) -> int:
    base = api_base.rstrip("/")
    url = f"{base}/v1/openclaw/me"
    headers = {"Authorization": f"Bearer {token}"}
    status, payload = http_request_json(
        url,
        "GET",
        None,
        headers=headers,
        retries=SUBMIT_RETRIES,
        retry_backoff_seconds=1.5,
    )
    if status == 200:
        agent_id = extract_agent_id(payload)
        if agent_id is None:
            raise RuntimeError(f"openclaw/me missing agent_id: {payload}")
        return agent_id
    if auto_create and status == 404 and isinstance(payload, dict) and "agent not found" in str(payload.get("error") or ""):
        return create_openclaw_agent(api_base, token)
    raise RuntimeError(f"openclaw/me HTTP {status}: {payload}")


def match_candidate_option_keys(
    payload: Any,
    topic_id: int,
    stance: str,
    want_prediction_id: Optional[int] = None,
    want_onchain_prediction_id: Optional[int] = None,
) -> Tuple[Optional[int], Set[str], str]:
    """Return (matched_prediction_id, allowed_option_keys, match_reason)."""
    rows = candidate_rows_from_payload(payload)
    if not rows:
        return None, set(), "no_candidates"

    def row_topic_id(row: Dict[str, Any]) -> Optional[int]:
        v = row.get("topic_id")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def row_onchain_prediction_id(row: Dict[str, Any]) -> Optional[int]:
        v = row.get("onchain_prediction_id") or row.get("owner_judgement_prediction_id")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    candidates: List[Tuple[Dict[str, Any], str]] = []
    for row in rows:
        pid = prediction_id_from_candidate_row(row)
        if pid is None:
            continue
        allowed = set(option_keys_from_candidate_row(row))
        r_topic = row_topic_id(row)
        if r_topic is not None and int(r_topic) == int(topic_id) and (not allowed or stance in allowed):
            candidates.append((row, "topic+stance"))
        elif (
            want_onchain_prediction_id is not None
            and row_onchain_prediction_id(row) is not None
            and int(row_onchain_prediction_id(row)) == int(want_onchain_prediction_id)
            and (not allowed or stance in allowed)
        ):
            candidates.append((row, "onchain_prediction_id"))
        elif want_prediction_id is not None and int(pid) == int(want_prediction_id):
            candidates.append((row, "prediction_id"))

    if len(candidates) == 1:
        row, reason = candidates[0]
        return prediction_id_from_candidate_row(row), set(option_keys_from_candidate_row(row)), reason

    if len(candidates) > 1:
        for row, reason in candidates:
            allowed = set(option_keys_from_candidate_row(row))
            if allowed and stance in allowed:
                return prediction_id_from_candidate_row(row), allowed, reason
        row, reason = candidates[0]
        return prediction_id_from_candidate_row(row), set(option_keys_from_candidate_row(row)), reason

    if len(rows) == 1:
        row = rows[0]
        return prediction_id_from_candidate_row(row), set(option_keys_from_candidate_row(row)), "single_candidate_fallback"

    return None, set(), "ambiguous_candidates"


def confidence_from_forecast(forecast: Dict[str, Any], stance: str) -> int:
    for row in forecast.get("normalized_scores") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("option")) == str(stance):
            try:
                s = float(row.get("score") or 0) * 100.0
                return max(0, min(100, int(round(s))))
            except (TypeError, ValueError):
                continue
    return 50


def reasoning_steps_from_bundle(bundle: Dict[str, Any]) -> List[str]:
    forecast = bundle.get("forecast") or {}
    sp = bundle.get("submit_payload_suggested") or {}
    rc = sp.get("reasoning_chain") if isinstance(sp, dict) else None
    if isinstance(rc, dict):
        kp = rc.get("key_points")
        if isinstance(kp, list):
            out = [str(x) for x in kp[:10] if x is not None]
            if out:
                return out
    steps: List[str] = []
    for row in forecast.get("evidence_ledger") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        claim = str(row.get("claim") or "")[:200]
        if cid or claim:
            steps.append(f"{cid}: {claim}".strip(": "))
        if len(steps) >= 10:
            break
    if not steps:
        fa = forecast.get("final_answer")
        if fa is not None:
            steps = [f"final_answer: {fa}"]
    return steps[:10]


def build_reasoning_chain(bundle: Dict[str, Any]) -> Dict[str, Any]:
    forecast = bundle.get("forecast") if isinstance(bundle.get("forecast"), dict) else {}
    sp = bundle.get("submit_payload_suggested") if isinstance(bundle.get("submit_payload_suggested"), dict) else {}
    rc = sp.get("reasoning_chain") if isinstance(sp.get("reasoning_chain"), dict) else {}

    summary = str(rc.get("thesis") or forecast.get("reasoning_text") or "").strip()
    if not summary:
        key_points = rc.get("key_points") if isinstance(rc.get("key_points"), list) else []
        summary = " ".join(str(x).strip() for x in key_points[:3] if x is not None).strip()
    if not summary:
        summary = str(forecast.get("final_answer") or "").strip()

    evidence: List[Dict[str, Any]] = []
    source_details = rc.get("source_details") if isinstance(rc.get("source_details"), list) else []
    for row in source_details:
        if not isinstance(row, dict):
            continue
        item = {
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("url") or "").strip(),
        }
        if item["title"] or item["url"]:
            evidence.append(item)
        if len(evidence) >= 20:
            break

    if not evidence:
        for row in forecast.get("evidence_ledger") or []:
            if not isinstance(row, dict):
                continue
            item = {
                "id": str(row.get("id") or "").strip(),
                "claim": str(row.get("claim") or "").strip(),
                "source": str(row.get("source") or "").strip(),
            }
            if item["id"] or item["claim"] or item["source"]:
                evidence.append(item)
            if len(evidence) >= 20:
                break

    return {
        "summary": truncate_content(summary or "No summary", 1200),
        "evidence": evidence,
    }


def truncate_content(text: str, max_len: int = 2000) -> str:
    return text


def _wallet_variant_seed(wallet_label: str, topic_id: int) -> int:
    raw = f"{wallet_label}|{topic_id}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16)


def _wallet_variant_pick(wallet_label: str, topic_id: int, choices: List[str]) -> str:
    rng = random.Random(_wallet_variant_seed(wallet_label, topic_id))
    return choices[rng.randrange(len(choices))]


def _wallet_variant_reorder(wallet_label: str, topic_id: int, items: List[str]) -> List[str]:
    out = [x for x in items if x]
    rng = random.Random(_wallet_variant_seed(wallet_label, topic_id) ^ 0xABCDEF)
    rng.shuffle(out)
    return out


def english_payload_text(bundle: Dict[str, Any], stance: str, wallet_label: str = "") -> str:
    sp = bundle.get("submit_payload_suggested") if isinstance(bundle.get("submit_payload_suggested"), dict) else {}
    rc = sp.get("reasoning_chain") if isinstance(sp.get("reasoning_chain"), dict) else {}
    thesis = str(rc.get("thesis") or "").strip()
    key_points = rc.get("key_points") if isinstance(rc.get("key_points"), list) else []
    english_points = [str(x).strip() for x in key_points if isinstance(x, str) and all(ord(ch) < 128 for ch in x)]
    topic_id = int((bundle.get("topic") or {}).get("id") or 0)
    if thesis and all(ord(ch) < 128 for ch in thesis):
        lead = _wallet_variant_pick(wallet_label, topic_id, [
            "Base case:",
            "Current read:",
            "My working view:",
            "Main call:",
        ])
        paragraphs = [p.strip() for p in thesis.split("\n\n") if p.strip()]
        paragraphs = _wallet_variant_reorder(wallet_label, topic_id, paragraphs[:2]) + paragraphs[2:]
        return truncate_content(f"{lead} " + "\n\n".join(paragraphs), 2000)
    if english_points:
        ordered = _wallet_variant_reorder(wallet_label, topic_id, english_points[:4])
        lead = _wallet_variant_pick(wallet_label, topic_id, [
            "Signal summary:",
            "Most useful cues:",
            "Why this side leads:",
        ])
        return truncate_content(f"{lead} " + " ".join(ordered), 2000)
    forecast = bundle.get("forecast") if isinstance(bundle.get("forecast"), dict) else {}
    multidim = forecast.get("rubric_multidim_analysis") if isinstance(forecast.get("rubric_multidim_analysis"), dict) else {}
    if isinstance(multidim.get("reasoning_narrative"), str) and multidim.get("reasoning_narrative").strip():
        return truncate_content(str(multidim.get("reasoning_narrative")).strip(), 2000)
    final_answer = str(forecast.get("final_answer") or stance or "prediction").strip() or "prediction"
    confidence = confidence_from_forecast(forecast, stance)
    prefix = _wallet_variant_pick(wallet_label, topic_id, [
        "I lean",
        "My call is",
        "I currently favor",
        "I would submit",
    ])
    suffix = _wallet_variant_pick(wallet_label, topic_id, [
        "based on the current evidence mix.",
        "given the present market and evidence setup.",
        "after weighing the available signals.",
        "using the current cross-source read.",
    ])
    return truncate_content(
        f"{prefix} {final_answer} with confidence score {confidence}/100 {suffix}",
        2000,
    )


def english_reasoning_chain(bundle: Dict[str, Any], stance: str, wallet_label: str = "") -> Dict[str, Any]:
    sp = bundle.get("submit_payload_suggested") if isinstance(bundle.get("submit_payload_suggested"), dict) else {}
    rc = sp.get("reasoning_chain") if isinstance(sp.get("reasoning_chain"), dict) else {}
    source_details = rc.get("source_details") if isinstance(rc.get("source_details"), list) else []
    evidence = []
    for row in source_details:
        if not isinstance(row, dict):
            continue
        item = {
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("url") or "").strip(),
        }
        if item["title"] or item["url"]:
            evidence.append(item)
        if len(evidence) >= 20:
            break
    forecast = bundle.get("forecast") if isinstance(bundle.get("forecast"), dict) else {}
    multidim = forecast.get("rubric_multidim_analysis") if isinstance(forecast.get("rubric_multidim_analysis"), dict) else {}
    topic_id = int((bundle.get("topic") or {}).get("id") or 0)
    summary = str(multidim.get("decision_summary") or english_payload_text(bundle, stance, wallet_label)).strip()
    thesis = str(rc.get("thesis") or forecast.get("reasoning_text") or summary).strip()
    sources = rc.get("sources") if isinstance(rc.get("sources"), list) else []
    key_points = rc.get("key_points") if isinstance(rc.get("key_points"), list) else []
    ordered_points = _wallet_variant_reorder(wallet_label, topic_id, [str(x).strip() for x in key_points if str(x).strip()])
    return {
        "summary": truncate_content(summary, 1200),
        "thesis": truncate_content(thesis, 20000),
        "sources": [str(x).strip() for x in sources if str(x).strip()][:50],
        "key_points": [truncate_content(str(x).strip(), 1000) for x in ordered_points][:50],
        "evidence": evidence,
    }


def extract_opinion_id(status: int, resp: Any) -> Optional[int]:
    if not isinstance(resp, dict):
        return None
    for key in ("opinion_id", "id"):
        v = resp.get(key)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    data = resp.get("data")
    if isinstance(data, dict):
        for key in ("opinion_id", "id"):
            v = data.get(key)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
    return None


def submit_openclaw_one_wallet(
    cfg: Dict[str, Any],
    bundle: Dict[str, Any],
    wallet_entry: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    """POST /v1/openclaw/opinions with SIWE JWT; optional confirm."""
    api_base = str(cfg["api_base"])
    oc = cfg.get("openclaw") or {}
    client = str(oc.get("client") or "openclaw")
    client_ver = str(oc.get("client_version") or "0.1.0")
    confirm_mode = str(oc.get("confirm_mode") or "auto_if_allowed")

    sess = login_wallet(api_base, wallet_entry)
    topic_id = int(bundle["topic"]["id"])
    sp = bundle.get("submit_payload_suggested") or {}
    prediction_id = sp.get("prediction_id")
    if prediction_id is None:
        raise ValueError("bundle missing submit_payload_suggested.prediction_id")
    prediction_id = int(prediction_id)
    onchain_prediction_id = sp.get("onchain_prediction_id")
    if onchain_prediction_id is not None:
        try:
            onchain_prediction_id = int(onchain_prediction_id)
        except (TypeError, ValueError):
            onchain_prediction_id = None
    stance = str(sp.get("option_key") or "").strip()
    if not stance:
        raise ValueError("bundle missing submit_payload_suggested.option_key (stance)")

    forecast = bundle.get("forecast") or {}
    if not isinstance(forecast, dict):
        forecast = {}

    result: Dict[str, Any] = {
        "address": sess["address"],
        "label": sess["label"],
        "mode": "openclaw",
        "dry_run": dry_run,
        "topic_id": topic_id,
        "prediction_id": prediction_id,
    }

    if dry_run:
        result["note"] = "dry_run: no openclaw network submit"
        result["ok"] = True
        return result

    agent_id = fetch_openclaw_me(api_base, sess["token"])
    result["agent_id"] = agent_id

    c_status, c_body = fetch_openclaw_candidates(api_base, sess["token"], topic_id)
    result["candidates_http_status"] = c_status
    if c_status != 200:
        result["ok"] = False
        result["error"] = f"candidates HTTP {c_status}: {c_body}"
        return result

    matched, allowed, match_reason = match_candidate_option_keys(
        c_body,
        topic_id=topic_id,
        stance=stance,
        want_prediction_id=prediction_id,
        want_onchain_prediction_id=onchain_prediction_id,
    )
    result["candidate_match_reason"] = match_reason
    result["candidate_prediction_id"] = matched
    if matched is None:
        result["ok"] = False
        result["error"] = (
            f"no matching candidate for topic {topic_id} (local prediction_id={prediction_id}, onchain_prediction_id={onchain_prediction_id}, reason={match_reason})"
        )
        return result
    if allowed and stance not in allowed:
        result["ok"] = False
        result["error"] = (
            f"stance {stance!r} not in candidates options {sorted(allowed)}"
        )
        return result

    body_json: Dict[str, Any] = {
        "agent_id": agent_id,
        "prediction_id": matched,
        "content": english_payload_text(bundle, stance, sess["label"]),
        "stance": stance,
        "confidence_score": confidence_from_forecast(forecast, stance),
        "reasoning_chain": english_reasoning_chain(bundle, stance, sess["label"]),
        "confirm_mode": confirm_mode,
    }

    run_id = f"skill-run-{int(time.time())}"
    url = api_base.rstrip("/") + "/v1/openclaw/opinions"
    headers = {
        "Authorization": f"Bearer {sess['token']}",
        "X-Lifefun-Client": client,
        "X-Lifefun-Client-Version": client_ver,
        "X-Lifefun-Run-Id": run_id,
        "X-Lifefun-Agent-Id": str(agent_id),
    }

    status, resp = http_request_json(
        url,
        "POST",
        body_json,
        headers=headers,
        retries=SUBMIT_RETRIES,
        retry_backoff_seconds=1.5,
    )
    result["opinion_http_status"] = status
    result["opinion_response"] = resp

    opinion_id: Optional[int] = None
    if status in (200, 201):
        opinion_id = extract_opinion_id(status, resp)
    elif status == 409:
        opinion_id = extract_opinion_id(status, resp)
        result["note"] = "409 conflict: using existing opinion_id if parsed"
        if opinion_id is None:
            result["ok"] = False
            result["error"] = f"409 conflict but could not parse opinion_id: {resp}"
            return result
    else:
        result["ok"] = False
        result["error"] = f"opinion POST HTTP {status}: {resp}"
        return result

    if opinion_id is not None:
        result["opinion_id"] = opinion_id

    status_text = ""
    auto_confirmed = False
    if isinstance(resp, dict):
        status_text = str(resp.get("status") or "").strip().lower()
        auto_confirmed = resp.get("auto_confirmed") is True
        data = resp.get("data")
        if isinstance(data, dict):
            if not status_text:
                status_text = str(data.get("status") or "").strip().lower()
            if not auto_confirmed:
                auto_confirmed = data.get("auto_confirmed") is True
    already_confirmed = status_text == "confirmed" or auto_confirmed

    if opinion_id is not None and not already_confirmed:
        c_url = f"{api_base.rstrip('/')}/v1/openclaw/opinions/{opinion_id}/confirm"
        c_st, c_resp = http_request_json(
            c_url,
            "POST",
            None,
            headers=headers,
            retries=SUBMIT_RETRIES,
            retry_backoff_seconds=1.5,
        )
        result["confirm_http_status"] = c_st
        result["confirm_response"] = c_resp
        if c_st >= 400:
            result["ok"] = False
            result["error"] = f"confirm HTTP {c_st}: {c_resp}"
            return result

    result["ok"] = True
    return result


def submit_one_wallet(
    cfg: Dict[str, Any],
    bundle: Dict[str, Any],
    wallet_entry: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    mode = str(cfg.get("submit", {}).get("mode") or "artifact")
    artifacts = Path(cfg["artifacts_dir"]).expanduser()
    ensure_dir(artifacts)

    wallet_label = str(wallet_entry.get("label") or "")
    try:
        if mode == "openclaw":
            return submit_openclaw_one_wallet(cfg, bundle, wallet_entry, dry_run)

        sess = login_wallet(str(cfg["api_base"]), wallet_entry)
    except Exception as e:
        acct = wallet_account(wallet_entry)
        return {
            "address": acct.address,
            "label": wallet_label or acct.address[:10],
            "mode": mode,
            "dry_run": dry_run,
            "ok": False,
            "error": str(e),
            "stage": "auth_or_submit",
        }
    topic_id = int(bundle["topic"]["id"])
    payload = build_http_body(bundle, sess)

    result: Dict[str, Any] = {
        "address": sess["address"],
        "label": sess["label"],
        "mode": mode,
        "dry_run": dry_run,
    }

    if dry_run:
        result["note"] = "dry_run: no network submit"
        return result

    if mode == "http":
        http_cfg = cfg["submit"].get("http") or {}
        url = str(http_cfg.get("url") or "").strip()
        if not url:
            raise ValueError("submit.mode=http requires submit.http.url")
        method = str(http_cfg.get("method") or "POST").upper()
        headers = dict(http_cfg.get("headers") or {})
        headers["Authorization"] = f"Bearer {sess['token']}"
        status, resp = http_request_json(url, method, payload, headers=headers)
        result["http_status"] = status
        result["http_response"] = resp
        if status >= 400:
            result["ok"] = False
        else:
            result["ok"] = True
        return result

    if mode == "hook":
        hook = cfg["submit"].get("hook") or {}
        cmd = list(hook.get("command") or [])
        if not cmd:
            raise ValueError("submit.mode=hook requires submit.hook.command")
        env = os.environ.copy()
        env["EVO_SUBMIT_JSON"] = json.dumps(payload, ensure_ascii=False)
        env["EVO_JWT"] = sess["token"]
        timeout = int(hook.get("timeout_seconds") or 120)
        proc = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout, text=True)
        result["hook_exit"] = proc.returncode
        result["hook_stdout"] = proc.stdout[-4000:]
        result["hook_stderr"] = proc.stderr[-4000:]
        result["ok"] = proc.returncode == 0
        return result

    # artifact (default): write per-wallet JSON for manual upload / later hook
    out_path = artifacts / f"submit_{topic_id}_{sess['address']}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result["artifact_path"] = str(out_path)
    result["ok"] = True
    return result


def write_submit_run_log(artifacts: Path, topic_id: int, prediction_id: int, results: List[Dict[str, Any]], ok_count: int) -> Path:
    logs_dir = artifacts / "submit_logs"
    ensure_dir(logs_dir)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out = logs_dir / f"submit_run_topic_{topic_id}_{stamp}.json"
    payload = {
        "topic_id": topic_id,
        "prediction_id": prediction_id,
        "ok_count": ok_count,
        "results": results,
        "written_at": stamp,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=skill_root() / "config" / "evo_config.json")
    parser.add_argument("--bundle", type=Path, required=True, help="Output from evo_predict.py")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-wallets", type=int, default=10)
    parser.add_argument("--chain-id", type=int, default=0, help="Override config chain_id for serial multi-chain runs")
    parser.add_argument("--force-resubmit", action="store_true", help="Ignore local cursor skip checks and resubmit anyway")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.chain_id:
        cfg = config_for_chain(cfg, args.chain_id)
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    topic_id = int(bundle["topic"]["id"])
    mode = str(cfg.get("submit", {}).get("mode") or "artifact")

    state_path = Path(cfg["state_file"]).expanduser()
    evo_cursor = load_cursor(state_path)
    oc_path = openclaw_cursor_path(cfg)
    oc_cursor = load_cursor(oc_path) if mode == "openclaw" else {"version": 1, "submitted": []}

    sp = bundle.get("submit_payload_suggested") or {}
    prediction_id_opt = sp.get("prediction_id")
    if mode == "openclaw" and prediction_id_opt is None:
        print(
            json.dumps({"error": "openclaw mode requires submit_payload_suggested.prediction_id"}),
            file=sys.stderr,
        )
        sys.exit(1)
    prediction_id = int(prediction_id_opt) if prediction_id_opt is not None else 0

    wallets = load_wallets(cfg)[: max(1, args.max_wallets)]
    pending: List[Dict[str, Any]] = []
    for w in wallets:
        acct = wallet_account(w)
        if not args.force_resubmit:
            if mode == "openclaw":
                if already_submitted_openclaw(oc_cursor, prediction_id, acct.address):
                    continue
            else:
                if already_submitted(evo_cursor, topic_id, acct.address):
                    continue
        pending.append(w)

    if not pending:
        note = (
            "all wallets already submitted for openclaw prediction_id"
            if mode == "openclaw"
            else "all wallets already submitted for topic"
        )
        print(
            json.dumps(
                {
                    "note": note,
                    "topic_id": topic_id,
                    "prediction_id": prediction_id if mode == "openclaw" else None,
                },
                indent=2,
            )
        )
        return

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(10, len(pending))) as ex:
        futs = {
            ex.submit(submit_one_wallet, cfg, bundle, entry, args.dry_run): entry
            for entry in pending
        }
        for fut in as_completed(futs):
            entry = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                acct = wallet_account(entry)
                results.append(
                    {
                        "address": acct.address,
                        "label": str(entry.get("label") or acct.address[:10]),
                        "mode": mode,
                        "dry_run": args.dry_run,
                        "ok": False,
                        "error": str(e),
                        "stage": "future_result",
                    }
                )

    ok_count = sum(1 for r in results if r.get("ok") is True)

    if mode == "openclaw":
        for r in results:
            if r.get("ok") and not args.dry_run:
                oid = r.get("opinion_id")
                oc_cursor.setdefault("submitted", []).append(
                    {
                        "topic_id": topic_id,
                        "prediction_id": int(r.get("prediction_id") or prediction_id),
                        "opinion_id": oid,
                        "address": r["address"],
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "mode": "openclaw",
                    }
                )
        save_cursor(oc_path, oc_cursor)
    else:
        for r in results:
            if r.get("ok") and not args.dry_run:
                evo_cursor.setdefault("submitted", []).append(
                    {
                        "topic_id": topic_id,
                        "address": r["address"],
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "mode": r.get("mode"),
                    }
                )
        save_cursor(state_path, evo_cursor)

    log_path = write_submit_run_log(
        Path(cfg["artifacts_dir"]).expanduser(),
        topic_id,
        prediction_id if mode == "openclaw" else 0,
        results,
        ok_count,
    )

    print(
        json.dumps(
            {
                "topic_id": topic_id,
                "prediction_id": prediction_id if mode == "openclaw" else None,
                "results": results,
                "ok_count": ok_count,
                "log_path": str(log_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
