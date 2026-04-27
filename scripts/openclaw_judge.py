#!/usr/bin/env python3
"""Send on-chain judgement tx via cast (foundry)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from evo_common import load_config, skill_root


def chain_config(cfg: Dict[str, Any], chain_id: int) -> Optional[Dict[str, Any]]:
    oc = cfg.get("openclaw") or {}
    for row in oc.get("chains") or []:
        if not isinstance(row, dict):
            continue
        if int(row.get("chain_id") or 0) == int(chain_id):
            return row
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="cast send judge(...) for OpenClaw opinion")
    parser.add_argument("--config", type=Path, default=skill_root() / "config" / "evo_config.json")
    parser.add_argument("--chain-id", type=int, required=True, help="e.g. 97 (BNB testnet) or 16602 (0G)")
    parser.add_argument("--opinion-id", type=int, required=True)
    parser.add_argument("--agent-token-id", type=int, required=True)
    parser.add_argument(
        "--disagree",
        action="store_true",
        help="disagree with opinion (default is agree=true)",
    )
    args = parser.parse_args()

    agree = not args.disagree

    cfg = load_config(args.config)
    ch = chain_config(cfg, args.chain_id)
    if not ch:
        print(f"No openclaw.chains entry for chain_id={args.chain_id}", file=sys.stderr)
        sys.exit(1)

    router = str(ch.get("router") or "").strip()
    owner_pid = ch.get("owner_judgement_prediction_id")
    if not router or owner_pid is None:
        print("chain config missing router or owner_judgement_prediction_id", file=sys.stderr)
        sys.exit(1)
    owner_pid = int(owner_pid)

    rpc_env = str(ch.get("rpc_url_env") or "").strip()
    if not rpc_env:
        print("chain config missing rpc_url_env", file=sys.stderr)
        sys.exit(1)
    rpc_url = os.environ.get(rpc_env, "").strip()
    if not rpc_url:
        print(f"Set {rpc_env} to RPC URL for chain {args.chain_id}", file=sys.stderr)
        sys.exit(1)

    pk = os.environ.get("OPENCLAW_PRIVATE_KEY", "").strip()
    if not pk:
        print("Set OPENCLAW_PRIVATE_KEY (hex private key)", file=sys.stderr)
        sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    sig = "judge(uint256,uint256,bool,uint256)"
    cmd = [
        "cast",
        "send",
        router,
        sig,
        str(owner_pid),
        str(int(args.agent_token_id)),
        "true" if agree else "false",
        str(int(args.opinion_id)),
        "--rpc-url",
        rpc_url,
        "--private-key",
        pk,
    ]
    print("+", " ".join(cmd[:6] + ["..."] + cmd[-4:]), file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0:
        sys.exit(proc.returncode)
    print("OK: cast send completed", file=sys.stderr)


if __name__ == "__main__":
    main()
