#!/usr/bin/env python3
"""Extract USER_ACTION_ROUTER_ABI from lifefun-frontend userActionRouter.abi.ts to JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def extract_abi_array(ts_text: str) -> list:
    m = re.search(
        r"export\s+const\s+USER_ACTION_ROUTER_ABI\s*=\s*(\[[\s\S]*?\])\s*as\s+const",
        ts_text,
    )
    if not m:
        raise ValueError("USER_ACTION_ROUTER_ABI not found in source")
    blob = m.group(1)
    blob = re.sub(r",(\s*[\]}])", r"\1", blob)
    return json.loads(blob)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync UserActionRouter ABI from frontend TS to JSON")
    ap.add_argument(
        "source",
        nargs="?",
        help="Path to userActionRouter.abi.ts",
    )
    ap.add_argument(
        "-o",
        "--out",
        default="",
        help="Output JSON path (default: rubric_forecast/contracts/user_action_router.json under repo root)",
    )
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    default_src = root.parent / "lifefun-frontend" / "src" / "lib" / "contracts" / "userActionRouter.abi.ts"
    src = Path(args.source) if args.source else default_src
    if not src.is_file():
        print(f"source not found: {src}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else root / "rubric_forecast" / "contracts" / "user_action_router.json"
    data = extract_abi_array(src.read_text(encoding="utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
