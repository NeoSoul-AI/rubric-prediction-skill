#!/usr/bin/env python3
"""End-to-end: feed or topic list → rubric → (optional) multi-wallet submit."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from evo_auth import load_wallets, wallet_account
from evo_chain_config import config_for_chain, configured_chains
from evo_common import ensure_dir, load_config, skill_root
from evo_feed_ingest import fetch_feed, fetch_prediction_for_topic, fetch_topic
from evo_submit import load_cursor, openclaw_cursor_path, openclaw_topic_complete_for_wallets


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), file=sys.stderr)
    subprocess.check_call(cmd)


def parse_topic_ids(topic_ids_arg: str, topic_id_single: int) -> List[int]:
    if topic_ids_arg and topic_ids_arg.strip():
        out: List[int] = []
        for part in topic_ids_arg.split(","):
            p = part.strip()
            if p:
                out.append(int(p))
        return out
    if topic_id_single:
        return [topic_id_single]
    return []


def parse_live_type_limits(spec: str) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    if not spec or not spec.strip():
        return out
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"invalid live-type-limit entry: {part!r}; expected <topic_type>:<count>")
        topic_type, count_text = part.split(":", 1)
        topic_type = topic_type.strip().lower()
        if not topic_type:
            raise ValueError(f"invalid live-type-limit entry: {part!r}; empty topic_type")
        count = int(count_text.strip())
        if count <= 0:
            raise ValueError(f"invalid live-type-limit entry: {part!r}; count must be > 0")
        out.append((topic_type, count))
    return out


def select_live_topic_ids(cfg: Dict[str, Any], type_limits: List[Tuple[str, int]]) -> Dict[str, Any]:
    feed = fetch_feed(cfg)
    items = feed.get("items") or []
    if not isinstance(items, list):
        raise RuntimeError("feed items missing or invalid")

    grouped: Dict[str, List[int]] = defaultdict(list)
    seen: set[int] = set()
    allow = {str(v).lower() for v in (cfg.get("feed") or {}).get("topic_status_allowlist", ["open", "locked"])}

    for row in items:
        if not isinstance(row, dict) or row.get("type") != "topic":
            continue
        status = str(row.get("status") or "").lower()
        if allow and status not in allow:
            continue
        tid = row.get("topic_id") or row.get("id")
        if tid is None:
            continue
        tid_int = int(tid)
        if tid_int in seen:
            continue
        seen.add(tid_int)
        topic_type = str(row.get("topic_type") or row.get("category") or "").strip().lower()
        if not topic_type:
            continue
        grouped[topic_type].append(tid_int)

    selected_ids: List[int] = []
    selected_by_type: Dict[str, List[int]] = {}
    available_by_type: Dict[str, int] = {k: len(v) for k, v in grouped.items()}
    missing: List[Dict[str, Any]] = []
    for topic_type, limit in type_limits:
        chosen = grouped.get(topic_type, [])[:limit]
        selected_by_type[topic_type] = chosen
        selected_ids.extend(chosen)
        if len(chosen) < limit:
            missing.append({"topic_type": topic_type, "requested": limit, "available": len(grouped.get(topic_type, []))})

    return {
        "topic_ids": selected_ids,
        "selected_by_type": selected_by_type,
        "available_by_type": available_by_type,
        "missing": missing,
        "feed_meta": {
            "limit": (cfg.get("feed") or {}).get("limit"),
            "chain_id": cfg.get("chain_id"),
        },
    }


def prediction_id_from_row(row: Optional[Dict[str, Any]]) -> Optional[int]:
    if not row or not isinstance(row, dict):
        return None
    v = row.get("id")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _artifact_suffix(chain_id: int) -> str:
    return f"_{chain_id}" if chain_id else ""


def _load_predict_snapshot(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_result_brief(predict_path: Path) -> Dict[str, Any]:
    bundle = _load_predict_snapshot(predict_path)
    forecast = bundle.get("forecast") if isinstance(bundle.get("forecast"), dict) else {}
    return {
        "artifact": str(predict_path),
        "topic_id": (bundle.get("topic") or {}).get("id"),
        "prediction_id": (bundle.get("prediction") or {}).get("id"),
        "chain_id": (bundle.get("topic") or {}).get("chain_id"),
        "chain_name": (bundle.get("topic") or {}).get("chain_name"),
        "title": (bundle.get("rubric_input") or {}).get("question"),
        "final_answer": forecast.get("final_answer"),
        "normalized_scores": forecast.get("normalized_scores"),
    }


def _write_summary(art: Path, name: str, payload: Dict[str, Any]) -> Path:
    out = art / name
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def run_pipeline_one_topic(
    root: Path,
    config_path: Path,
    art: Path,
    topic_id: int,
    dry_run: bool,
    skip_submit: bool,
    max_wallets: int,
    chain_id: int = 0,
) -> Dict[str, Any]:
    suffix = _artifact_suffix(chain_id)
    ingest_path = art / f"ingest_{topic_id}{suffix}.json"
    predict_path = art / f"predict_{topic_id}{suffix}.json"

    ingest_cmd = [
        sys.executable,
        str(root / "scripts" / "evo_feed_ingest.py"),
        "--config",
        str(config_path),
        "--topic-id",
        str(topic_id),
        "--out",
        str(ingest_path),
    ]
    if chain_id:
        ingest_cmd.extend(["--chain-id", str(chain_id)])
    run(ingest_cmd)

    run(
        [
            sys.executable,
            str(root / "scripts" / "evo_predict.py"),
            str(ingest_path),
            "--out",
            str(predict_path),
        ]
    )

    submit_result: Optional[str] = None
    if not skip_submit:
        submit_cmd = [
            sys.executable,
            str(root / "scripts" / "evo_submit.py"),
            "--config",
            str(config_path),
            "--bundle",
            str(predict_path),
            "--max-wallets",
            str(max_wallets),
        ]
        if chain_id:
            submit_cmd.extend(["--chain-id", str(chain_id)])
        if dry_run:
            submit_cmd.append("--dry-run")
        run(submit_cmd)
        submit_result = "dry_run" if dry_run else "submitted"
    elif predict_path.exists():
        submit_result = "skipped_submit"

    brief = _extract_result_brief(predict_path)
    brief.update({"ingest_artifact": str(ingest_path), "submit_result": submit_result})
    return brief


def run_legacy_feed_flow(
    root: Path,
    config_path: Path,
    art: Path,
    topic_id: int,
    dry_run: bool,
    skip_submit: bool,
    max_wallets: int,
    chain_id: int = 0,
) -> Dict[str, Any]:
    suffix = _artifact_suffix(chain_id)
    ingest_path = art / f"last_ingest{suffix}.json"
    predict_path = art / f"last_predict{suffix}.json"

    ingest_cmd = [
        sys.executable,
        str(root / "scripts" / "evo_feed_ingest.py"),
        "--config",
        str(config_path),
        "--out",
        str(ingest_path),
    ]
    if topic_id:
        ingest_cmd.extend(["--topic-id", str(topic_id)])
    if chain_id:
        ingest_cmd.extend(["--chain-id", str(chain_id)])
    run(ingest_cmd)

    run(
        [
            sys.executable,
            str(root / "scripts" / "evo_predict.py"),
            str(ingest_path),
            "--out",
            str(predict_path),
        ]
    )

    submit_result: Optional[str] = None
    if not skip_submit:
        submit_cmd = [
            sys.executable,
            str(root / "scripts" / "evo_submit.py"),
            "--config",
            str(config_path),
            "--bundle",
            str(predict_path),
            "--max-wallets",
            str(max_wallets),
        ]
        if chain_id:
            submit_cmd.extend(["--chain-id", str(chain_id)])
        if dry_run:
            submit_cmd.append("--dry-run")
        run(submit_cmd)
        submit_result = "dry_run" if dry_run else "submitted"
    else:
        submit_result = "skipped_submit"

    brief = _extract_result_brief(predict_path)
    brief.update({"ingest_artifact": str(ingest_path), "submit_result": submit_result, "mode": "legacy_feed"})
    return brief


def main() -> None:
    root = skill_root()
    default_cfg = root / "config" / "evo_config.json"
    art = root / "state" / "artifacts"
    ensure_dir(art)

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=default_cfg)
    parser.add_argument("--topic-id", type=int, default=0)
    parser.add_argument(
        "--topic-ids",
        type=str,
        default="",
        help="Comma-separated topic ids (batch). Example: 1582,1579,1619",
    )
    parser.add_argument("--dry-run", action="store_true", help="Submit step skips HTTP/hook")
    parser.add_argument("--skip-submit", action="store_true")
    parser.add_argument("--max-wallets", type=int, default=10)
    parser.add_argument("--all-chains", action="store_true", help="Run serially across configured chains")
    parser.add_argument("--chain-id", type=int, default=0, help="Run only one specified chain")
    parser.add_argument(
        "--live-type-limits",
        type=str,
        default="",
        help="Select topic ids from live feed by topic type, e.g. crypto:6,sports:3,geopolitics:6",
    )
    args = parser.parse_args()

    manual_topic_ids = parse_topic_ids(args.topic_ids, args.topic_id)
    live_type_limits = parse_live_type_limits(args.live_type_limits)
    cfg = load_config(args.config)

    if args.chain_id:
        chain_ids = [int(args.chain_id)]
    elif args.all_chains:
        chain_ids = [int(row.get("chain_id")) for row in configured_chains(cfg)]
    else:
        chain_ids = [int(cfg.get("chain_id") or 0)]

    topic_ids = manual_topic_ids
    live_selection: Dict[str, Any] = {}
    if live_type_limits:
        if manual_topic_ids:
            raise SystemExit("cannot combine manual --topic-id/--topic-ids with --live-type-limits")
        select_cfg = config_for_chain(cfg, chain_ids[0]) if chain_ids else cfg
        live_selection = select_live_topic_ids(select_cfg, live_type_limits)
        topic_ids = list(live_selection.get("topic_ids") or [])

    if not topic_ids:
        summaries = []
        for cid in chain_ids:
            brief = run_legacy_feed_flow(
                root,
                args.config,
                art,
                args.topic_id,
                args.dry_run,
                args.skip_submit,
                args.max_wallets,
                cid,
            )
            summaries.append(brief)
        payload = {"mode": "legacy_feed", "chains": summaries}
        summary_name = "summary_legacy_all_chains.json" if len(chain_ids) > 1 else f"summary_legacy_{chain_ids[0]}.json"
        out = _write_summary(art, summary_name, payload)
        payload["summary_artifact"] = str(out)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    mode = str(cfg.get("submit", {}).get("mode") or "artifact")
    wallets = load_wallets(cfg)[: max(1, args.max_wallets)]
    wallet_addrs = [wallet_account(w).address for w in wallets]

    oc_path = openclaw_cursor_path(cfg)
    oc_cursor = load_cursor(oc_path) if mode == "openclaw" else {"version": 1, "submitted": []}

    all_chain_summaries: List[Dict[str, Any]] = []
    batch_results: List[Dict[str, Any]] = []
    for cid in chain_ids:
        chain_cfg = config_for_chain(cfg, cid)
        chain_result_rows: List[Dict[str, Any]] = []
        summary: Dict[str, Any] = {
            "chain_id": cid,
            "chain_name": ((chain_cfg.get("openclaw") or {}).get("active_chain") or {}).get("name"),
            "total": len(topic_ids),
            "skipped_not_open": 0,
            "skipped_already_submitted": 0,
            "skipped_no_prediction": 0,
            "ok": 0,
            "failed": 0,
            "errors": [],
            "results": chain_result_rows,
        }

        for tid in topic_ids:
            try:
                topic = fetch_topic(chain_cfg, tid)
            except Exception as e:
                summary["failed"] += 1
                summary["errors"].append({"topic_id": tid, "phase": "fetch_topic", "error": str(e)})
                continue

            if str(topic.get("status") or "").lower() != "open":
                summary["skipped_not_open"] += 1
                print(f"[skip][chain {cid}] topic {tid} status={topic.get('status')!r} (not open)", file=sys.stderr)
                continue

            pred = fetch_prediction_for_topic(chain_cfg, tid)
            pid = prediction_id_from_row(pred)
            if pid is None:
                summary["skipped_no_prediction"] += 1
                print(f"[skip][chain {cid}] topic {tid}: no prediction id for chain", file=sys.stderr)
                continue

            if mode == "openclaw" and openclaw_topic_complete_for_wallets(oc_cursor, pid, wallet_addrs):
                summary["skipped_already_submitted"] += 1
                print(
                    f"[skip][chain {cid}] topic {tid} prediction_id={pid}: all wallets already in openclaw cursor",
                    file=sys.stderr,
                )
                continue

            try:
                brief = run_pipeline_one_topic(
                    root,
                    args.config,
                    art,
                    tid,
                    args.dry_run,
                    args.skip_submit,
                    args.max_wallets,
                    cid,
                )
                chain_result_rows.append(brief)
                batch_results.append(brief)
                summary["ok"] += 1
                if mode == "openclaw":
                    oc_cursor = load_cursor(oc_path)
            except subprocess.CalledProcessError as e:
                summary["failed"] += 1
                summary["errors"].append(
                    {"topic_id": tid, "phase": "pipeline", "error": f"exit {e.returncode}"}
                )
            except Exception as e:
                summary["failed"] += 1
                summary["errors"].append({"topic_id": tid, "phase": "pipeline", "error": str(e)})

        all_chain_summaries.append(summary)

    payload = {
        "mode": "batch_topics",
        "topic_ids": topic_ids,
        "chains": all_chain_summaries,
        "flat_results": batch_results,
    }
    if live_selection:
        payload["live_selection"] = live_selection
        payload["live_type_limits"] = [{"topic_type": topic_type, "count": count} for topic_type, count in live_type_limits]
    summary_name = "summary_batch_all_chains.json" if len(chain_ids) > 1 else f"summary_batch_{chain_ids[0]}.json"
    out = _write_summary(art, summary_name, payload)
    payload["summary_artifact"] = str(out)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
