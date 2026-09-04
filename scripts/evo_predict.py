#!/usr/bin/env python3
"""Run rubric_forecast.py on rubric_input and write forecast JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from evo_common import skill_root


def run_rubric(rubric_input: Dict[str, Any]) -> Dict[str, Any]:
    script = skill_root() / "scripts" / "rubric_forecast.py"
    if not script.exists():
        raise FileNotFoundError(script)
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(rubric_input).encode("utf-8"),
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"rubric_forecast.py exit {proc.returncode}: {err}")
    out_raw = proc.stdout.decode("utf-8")
    return json.loads(out_raw)


def rubric_to_reasoning_chain(forecast: Dict[str, Any]) -> Dict[str, Any]:
    if forecast.get("status") != "ok":
        return {
            "thesis": "insufficient_spec",
            "sources": [],
            "key_points": list(forecast.get("blocking_reasons") or [])[:12],
            "source_details": [],
        }
    thesis = str(forecast.get("reasoning_text") or forecast.get("final_answer") or "")
    sources: list[str] = []
    details: list[Dict[str, str]] = []
    key_points: list[str] = []

    for row in forecast.get("evidence_ledger") or []:
        if not isinstance(row, dict):
            continue
        claim = str(row.get("claim") or row.get("id") or "")
        src = str(row.get("source") or "")
        key_points.append(f"{row.get('id')}: {claim[:200]}")
        if src.startswith("http"):
            sources.append(src)
            details.append({"url": src, "title": claim[:120] or src})

    for row in forecast.get("normalized_scores") or []:
        if isinstance(row, dict):
            key_points.append(
                f"norm {row.get('option')}: {float(row.get('score') or 0):.4f}"
            )

    if not sources:
        sources = ["https://api.test.evoevo.ai/v1/topics/placeholder"]
    if not details:
        details = [{"url": sources[0], "title": "evidence"}]
    return {
        "thesis": thesis or str(forecast.get("final_answer")),
        "sources": sources[:25],
        "key_points": key_points[:20],
        "source_details": details[:25],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ingest_json", type=Path, help="Output from evo_feed_ingest.py")
    parser.add_argument("--out", type=Path, help="Write full bundle JSON")
    args = parser.parse_args()
    bundle = json.loads(args.ingest_json.read_text(encoding="utf-8"))
    rubric_in = bundle["rubric_input"]
    forecast = run_rubric(rubric_in)
    reasoning = rubric_to_reasoning_chain(forecast)
    out = {
        **bundle,
        "forecast": forecast,
        "submit_payload_suggested": {
            "topic_id": bundle["topic"]["id"],
            "prediction_id": (bundle.get("prediction") or {}).get("id"),
            "onchain_prediction_id": (bundle.get("prediction") or {}).get("onchain_prediction_id"),
            "owner_judgement_prediction_id": (bundle.get("prediction") or {}).get("owner_judgement_prediction_id"),
            "chain_id": bundle["topic"].get("chain_id"),
            "option_key": forecast.get("final_answer"),
            "reasoning_chain": reasoning,
        },
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
