#!/usr/bin/env python3
"""Wallet auth: SIWE-style message from EvoEvo → sign → JWT."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

AUTH_RETRIES = 3

from evo_common import http_request_json, load_config, skill_root

try:
    from eth_account import Account
    from eth_account.messages import encode_defunct
except ImportError:
    print(
        "Missing eth-account. Install: pip3 install --user -r "
        + str(skill_root() / "requirements-evo.txt"),
        file=sys.stderr,
    )
    raise


def normalize_private_key(raw: str) -> str:
    key = raw.strip()
    if not key.startswith("0x"):
        key = "0x" + key
    return key


def load_wallets(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = Path(cfg["wallets_file"]).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Wallets file missing: {path}\nCopy evo_wallets.example.json to evo_wallets.json and add keys."
        )
    with path.open("r", encoding="utf-8") as f:
        store = json.load(f)
    if isinstance(store, list):
        wallets = store
    else:
        wallets = store.get("wallets") if isinstance(store, dict) else None
    if not isinstance(wallets, list) or len(wallets) == 0:
        raise ValueError(
            "wallets file must be a JSON array or an object with non-empty 'wallets' array"
        )
    return wallets


def wallet_account(entry: Dict[str, Any]) -> Any:
    pk = normalize_private_key(str(entry["private_key"]))
    return Account.from_key(pk)


def fetch_nonce(api_base: str, address: str) -> Tuple[str, str]:
    url = api_base.rstrip("/") + "/v1/auth/nonce"
    status, payload = http_request_json(
        url,
        "POST",
        {"address": address},
        retries=AUTH_RETRIES,
        retry_backoff_seconds=1.2,
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"auth/nonce failed HTTP {status}: {payload}")
    message = str(payload.get("message") or "")
    nonce = str(payload.get("nonce") or "")
    if not message or not nonce:
        raise RuntimeError(f"auth/nonce missing fields: {payload}")
    return message, nonce


def sign_message(private_key: bytes, message: str) -> str:
    signed = Account.sign_message(encode_defunct(text=message), private_key)
    sig_hex = signed.signature.hex()
    return sig_hex if sig_hex.startswith("0x") else "0x" + sig_hex


def login(api_base: str, address: str, nonce: str, signature: str) -> str:
    url = api_base.rstrip("/") + "/v1/auth/login"
    status, payload = http_request_json(
        url,
        "POST",
        {"address": address, "nonce": nonce, "signature": signature},
        retries=AUTH_RETRIES,
        retry_backoff_seconds=1.2,
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"auth/login failed HTTP {status}: {payload}")
    token = payload.get("token")
    if not token:
        raise RuntimeError(f"auth/login missing token: {payload}")
    return str(token)


def login_wallet(api_base: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return {label, address, token, expires_at}."""
    acct = wallet_account(entry)
    address = acct.address
    message, nonce = fetch_nonce(api_base, address)
    sig = sign_message(acct.key, message)
    token = login(api_base, address, nonce, sig)
    return {
        "label": str(entry.get("label") or address[:10]),
        "address": address,
        "token": token,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="EvoEvo wallet login smoke test")
    parser.add_argument(
        "--config",
        type=Path,
        default=skill_root() / "config" / "evo_config.json",
    )
    args = parser.parse_args()
    cfg = load_config(args.config)
    api_base = str(cfg["api_base"])
    wallets = load_wallets(cfg)
    for w in wallets[:1]:
        sess = login_wallet(api_base, w)
        print(json.dumps({**sess, "token": sess["token"][:20] + "…"}, indent=2))
    print("OK: auth flow works for first wallet (set wallets_file for all 10).", file=sys.stderr)


if __name__ == "__main__":
    main()
