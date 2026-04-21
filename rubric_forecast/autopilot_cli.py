"""CLI for local keystore autopilot: auth, run, pause, status."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rubric_forecast.audit import AuditLogger
from rubric_forecast.config import AutopilotConfig, forbid_plaintext_private_key_in_env
from rubric_forecast.daemon import AutopilotDaemon, ensure_policy_file
from rubric_forecast.keystore import import_private_key_to_keystore
from rubric_forecast.openclaw_adapter import AdapterApiError, OpenClawAdapter


def _password_from_env() -> str:
    p = os.environ.get("AUTOPILOT_KEYSTORE_PASSWORD", "")
    return p


def _build_adapter(cfg: AutopilotConfig) -> OpenClawAdapter:
    return OpenClawAdapter(
        cfg.openclaw_base_url,
        api_key=cfg.openclaw_api_key,
        jwt_token=cfg.resolved_jwt(),
    )


def cmd_import_key(args: argparse.Namespace) -> int:
    forbid_plaintext_private_key_in_env()
    pk = args.private_key
    if args.private_key_file:
        pk = Path(args.private_key_file).read_text(encoding="utf-8").strip()
    if not pk:
        print("missing private key", file=sys.stderr)
        return 2
    pw = args.password
    if args.password_env:
        pw = os.environ.get(args.password_env, "")
    if not pw:
        print("missing password (use --password or --password-env)", file=sys.stderr)
        return 2
    cfg = AutopilotConfig.from_env()
    cfg.ensure_data_dir()
    path = import_private_key_to_keystore(pk, pw, cfg.keystore_path)
    print(f"keystore written: {path}")
    return 0


def cmd_init_policy(_args: argparse.Namespace) -> int:
    cfg = AutopilotConfig.from_env()
    cfg.ensure_data_dir()
    ensure_policy_file(cfg.policy_path)
    print(f"policy: {cfg.policy_path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    forbid_plaintext_private_key_in_env()
    cfg = AutopilotConfig.from_env()
    ensure_policy_file(cfg.policy_path)
    pw = _password_from_env()
    if not cfg.dry_run and not pw:
        print("set AUTOPILOT_KEYSTORE_PASSWORD for non-dry-run", file=sys.stderr)
        return 2
    daemon = AutopilotDaemon(cfg)
    if args.loop:
        daemon.loop_forever(keystore_password=pw)
        return 0
    summary = daemon.run_once(keystore_password=pw)
    print(json.dumps(summary, indent=2))
    return 0


def _resolve_signature(args: argparse.Namespace, cfg: AutopilotConfig) -> str:
    if args.signature:
        return str(args.signature).strip()
    if args.signature_file:
        return Path(args.signature_file).read_text(encoding="utf-8").strip()
    if cfg.lifefun_login_signature_source:
        sig_path = Path(cfg.lifefun_login_signature_source)
        if sig_path.is_file():
            return sig_path.read_text(encoding="utf-8").strip()
        env_sig = os.environ.get(cfg.lifefun_login_signature_source)
        if env_sig:
            return env_sig.strip()
    raise ValueError(
        "missing signature; provide --signature/--signature-file "
        "or set LIFEFUN_LOGIN_SIGNATURE_SOURCE"
    )


def cmd_auth_login(args: argparse.Namespace) -> int:
    cfg = AutopilotConfig.from_env()
    adapter = _build_adapter(cfg)
    address = args.address or cfg.lifefun_login_address
    if not address:
        print("missing address (use --address or LIFEFUN_LOGIN_ADDRESS)", file=sys.stderr)
        return 2
    try:
        nonce_data = adapter.auth_nonce(address)
        nonce = str(nonce_data.get("nonce"))
        normalized_address = str(nonce_data.get("address") or address)
        signature = _resolve_signature(args, cfg)
        login_data = adapter.auth_login(normalized_address, nonce, signature)
        token = login_data.get("token")
        if not isinstance(token, str) or not token:
            print("auth login response missing token", file=sys.stderr)
            return 1
        cfg.ensure_data_dir()
        cfg.lifefun_jwt_file_path.write_text(token, encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "address": normalized_address,
                    "jwt_file": str(cfg.lifefun_jwt_file_path),
                    "expires_at": login_data.get("expires_at"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (AdapterApiError, ValueError, RuntimeError) as e:
        print(f"auth-login failed: {e}", file=sys.stderr)
        return 1


def cmd_auth_check(_args: argparse.Namespace) -> int:
    cfg = AutopilotConfig.from_env()
    adapter = _build_adapter(cfg)
    out: dict[str, object] = {
        "base_url": cfg.openclaw_base_url,
        "jwt_ok": False,
        "openclaw_ok": False,
        "health_ok": False,
    }
    out["health_ok"] = adapter.health_ping()
    try:
        adapter.auth_heartbeat()
        out["jwt_ok"] = True
    except Exception as e:  # noqa: BLE001
        out["jwt_error"] = str(e)
    try:
        adapter.openclaw_me()
        out["openclaw_ok"] = True
    except Exception as e:  # noqa: BLE001
        out["openclaw_error"] = str(e)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if bool(out["jwt_ok"]) and bool(out["openclaw_ok"]) else 1


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = AutopilotConfig.from_env()
    audit = AuditLogger(cfg.audit_log_path)
    rows = audit.tail(30)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    st = cfg.state_path.read_text(encoding="utf-8") if cfg.state_path.is_file() else "{}"
    print("state:", st)
    print("paused:", cfg.pause_file_path.is_file())
    return 0


def cmd_pause(_args: argparse.Namespace) -> int:
    cfg = AutopilotConfig.from_env()
    AutopilotDaemon(cfg).pause()
    print("paused (touch file)")
    return 0


def cmd_resume(_args: argparse.Namespace) -> int:
    cfg = AutopilotConfig.from_env()
    AutopilotDaemon(cfg).resume()
    print("resumed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="rubric-autopilot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import-key", help="encrypt private key to keystore JSON")
    p_imp.add_argument("--private-key", help="0x-prefixed 32-byte hex")
    p_imp.add_argument("--private-key-file", help="read hex from file")
    p_imp.add_argument("--password", help="encryption password (prefer --password-env)")
    p_imp.add_argument("--password-env", help="env var holding password")
    p_imp.set_defaults(func=cmd_import_key)

    p_init = sub.add_parser("init-policy", help="write default policy.json if missing")
    p_init.set_defaults(func=cmd_init_policy)

    p_run = sub.add_parser("run", help="one cycle or loop")
    p_run.add_argument("--loop", action="store_true", help="poll forever")
    p_run.set_defaults(func=cmd_run)

    p_auth_login = sub.add_parser("auth-login", help="nonce+signature login and persist JWT")
    p_auth_login.add_argument("--address", help="wallet address")
    p_auth_login.add_argument("--signature", help="signed nonce message")
    p_auth_login.add_argument("--signature-file", help="read signature from file")
    p_auth_login.set_defaults(func=cmd_auth_login)

    sub.add_parser("auth-check", help="check JWT heartbeat + openclaw_me").set_defaults(
        func=cmd_auth_check
    )
    sub.add_parser("status", help="tail audit log + state").set_defaults(func=cmd_status)
    sub.add_parser("pause", help="create pause file").set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="remove pause file").set_defaults(func=cmd_resume)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
