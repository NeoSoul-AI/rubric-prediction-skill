"""Autopilot configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


@dataclass
class AutopilotConfig:
    """Runtime configuration for the local autopilot daemon."""

    data_dir: Path
    keystore_path: Path
    policy_path: Path
    state_path: Path
    audit_log_path: Path
    pause_file_path: Path
    candidates_file: Optional[Path]
    poll_interval_seconds: int
    dry_run: bool
    chain_id: int
    rpc_url: Optional[str]
    openclaw_base_url: Optional[str]
    openclaw_api_key: Optional[str]
    lifefun_jwt: Optional[str]
    lifefun_jwt_file_path: Path
    lifefun_login_address: Optional[str]
    lifefun_login_signature_source: Optional[str]
    bundler_url: Optional[str]
    execution_mode: str
    min_normalized_score: float
    max_actions_per_cycle: int

    @classmethod
    def from_env(cls) -> AutopilotConfig:
        home = Path.home()
        data_dir = Path(os.environ.get("AUTOPILOT_DATA_DIR", str(home / ".rubric_autopilot")))
        data_dir = data_dir.expanduser().resolve()
        keystore_name = os.environ.get("AUTOPILOT_KEYSTORE_FILE", "executor.keystore")
        return cls(
            data_dir=data_dir,
            keystore_path=Path(
                os.environ.get("AUTOPILOT_KEYSTORE_PATH", str(data_dir / keystore_name))
            ).expanduser().resolve(),
            policy_path=Path(
                os.environ.get("AUTOPILOT_POLICY_PATH", str(data_dir / "policy.json"))
            ).expanduser().resolve(),
            state_path=Path(
                os.environ.get("AUTOPILOT_STATE_PATH", str(data_dir / "state.json"))
            ).expanduser().resolve(),
            audit_log_path=Path(
                os.environ.get("AUTOPILOT_AUDIT_LOG", str(data_dir / "audit.jsonl"))
            ).expanduser().resolve(),
            pause_file_path=Path(
                os.environ.get("AUTOPILOT_PAUSE_FILE", str(data_dir / "PAUSED"))
            ).expanduser().resolve(),
            candidates_file=(
                Path(p).expanduser().resolve()
                if (p := os.environ.get("AUTOPILOT_CANDIDATES_FILE"))
                else None
            ),
            poll_interval_seconds=_env_int("AUTOPILOT_POLL_INTERVAL", 60),
            dry_run=_env_bool("AUTOPILOT_DRY_RUN", True),
            chain_id=_env_int("AUTOPILOT_CHAIN_ID", 56),
            rpc_url=os.environ.get("AUTOPILOT_RPC_URL") or os.environ.get("RPC_URL"),
            openclaw_base_url=os.environ.get("OPENCLAW_BASE_URL") or os.environ.get(
                "LIFEFUN_API_BASE_URL"
            ),
            openclaw_api_key=os.environ.get("OPENCLAW_API_KEY") or os.environ.get(
                "AUTOPILOT_OPENCLAW_KEY"
            ),
            lifefun_jwt=os.environ.get("LIFEFUN_JWT") or os.environ.get("AUTOPILOT_JWT"),
            lifefun_jwt_file_path=Path(
                os.environ.get("LIFEFUN_JWT_FILE", str(data_dir / "lifefun.jwt"))
            ).expanduser().resolve(),
            lifefun_login_address=os.environ.get("LIFEFUN_LOGIN_ADDRESS"),
            lifefun_login_signature_source=os.environ.get("LIFEFUN_LOGIN_SIGNATURE_SOURCE"),
            bundler_url=os.environ.get("AUTOPILOT_BUNDLER_URL"),
            execution_mode=(os.environ.get("AUTOPILOT_EXECUTION_MODE") or "eoa").strip().lower(),
            min_normalized_score=_env_float("AUTOPILOT_MIN_SCORE", 0.55),
            max_actions_per_cycle=_env_int("AUTOPILOT_MAX_ACTIONS_PER_CYCLE", 5),
        )

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolved_jwt(self) -> Optional[str]:
        if self.lifefun_jwt:
            return self.lifefun_jwt
        if self.lifefun_jwt_file_path.is_file():
            token = self.lifefun_jwt_file_path.read_text(encoding="utf-8").strip()
            return token or None
        return None


def forbid_plaintext_private_key_in_env() -> None:
    """Fail fast if a raw private key appears in environment (unsafe)."""
    banned = (
        "PRIVATE_KEY",
        "AUTOPILOT_PRIVATE_KEY",
        "EXECUTOR_PRIVATE_KEY",
        "ETH_PRIVATE_KEY",
    )
    for key in banned:
        val = os.environ.get(key)
        if val and val.strip().startswith("0x") and len(val.strip()) >= 66:
            raise RuntimeError(
                f"Refusing to start: {key} contains a plaintext private key. "
                "Remove it and use `rubric-autopilot import-key` with a keystore instead."
            )
