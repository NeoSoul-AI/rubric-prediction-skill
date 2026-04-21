"""Ethereum-compatible keystore: import private key, encrypt at rest, unlock in memory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

# eth_account is optional (autopilot extra)
_ACCOUNT = None  # type: ignore


def _require_eth_account():
    global _ACCOUNT
    if _ACCOUNT is not None:
        return _ACCOUNT
    try:
        from eth_account import Account as _A

        _ACCOUNT = _A
        return _ACCOUNT
    except ImportError as e:
        raise ImportError(
            "autopilot keystore requires optional deps: pip install 'rubric-forecasting[autopilot]'"
        ) from e


def normalize_private_key_hex(raw: str) -> str:
    s = raw.strip()
    if not s.startswith("0x"):
        s = "0x" + s
    if len(s) != 66:
        raise ValueError("private key must be 32 bytes hex (64 hex chars + 0x)")
    return s


def import_private_key_to_keystore(
    private_key_hex: str,
    password: str,
    keystore_path: Path,
    *,
    kdf: str = "scrypt",
) -> Path:
    """
    Encrypt private key and write JSON keystore (Ethereum keyfile format).
    Does not leave plaintext on disk.
    """
    Account = _require_eth_account()
    pk = normalize_private_key_hex(private_key_hex)
    account = Account.from_key(pk)
    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    encrypted: dict[str, Any] = Account.encrypt(account.key, password, kdf=kdf)
    encrypted["address"] = account.address
    keystore_path.write_text(json.dumps(encrypted, indent=2), encoding="utf-8")
    try:
        keystore_path.chmod(0o600)
    except OSError:
        pass
    return keystore_path


class UnlockedAccount:
    """Holds a decrypted account reference; call clear() when done."""

    def __init__(self, account: Any) -> None:
        self._account = account

    @property
    def address(self) -> str:
        return str(self._account.address)

    @property
    def key(self) -> bytes:
        return bytes(self._account.key)

    @property
    def account(self) -> Any:
        return self._account

    def clear(self) -> None:
        self._account = None


def unlock_keystore(keystore_path: Path, password: str) -> UnlockedAccount:
    """Decrypt keystore file into memory."""
    Account = _require_eth_account()
    if not keystore_path.is_file():
        raise FileNotFoundError(f"keystore not found: {keystore_path}")
    data = json.loads(keystore_path.read_text(encoding="utf-8"))
    private_key = Account.decrypt(data, password)
    account = Account.from_key(private_key)
    return UnlockedAccount(account)
