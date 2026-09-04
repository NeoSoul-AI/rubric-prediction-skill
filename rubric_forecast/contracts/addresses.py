"""Default UserActionRouter addresses by chain_id (aligned with lifefun-frontend)."""

from __future__ import annotations

import os
from typing import Dict, Optional

# BSC mainnet, BSC testnet, Zero-G testnet/mainnet defaults — override via env per chain.
_DEFAULT_USER_ACTION_ROUTER: Dict[int, str] = {
    56: "0x61bb71442749d13a4BB7257DfBFFf0452ae937f9",
    97: "0xc9D2431a0588E0402adcD17A4339789b04f8Ed88",
    16602: "0x1D942889Ff61b8Cd6a1400d732719bcF14a6dDf6",
    16601: "0x61bb71442749d13a4BB7257DfBFFf0452ae937f9",
}


def _env_override(chain_id: int) -> Optional[str]:
    raw = os.environ.get(f"USER_ACTION_ROUTER_ADDRESS_{chain_id}")
    if raw and raw.strip().startswith("0x") and len(raw.strip()) == 42:
        return raw.strip()
    generic = os.environ.get("USER_ACTION_ROUTER_ADDRESS")
    if generic and generic.strip().startswith("0x") and len(generic.strip()) == 42:
        return generic.strip()
    return None


def get_user_action_router_address(chain_id: int) -> str:
    """Resolve router address; env overrides take precedence."""
    override = _env_override(chain_id)
    if override:
        return override
    addr = _DEFAULT_USER_ACTION_ROUTER.get(chain_id)
    if not addr:
        raise ValueError(
            f"UserActionRouter not configured for chain_id={chain_id}; "
            "set USER_ACTION_ROUTER_ADDRESS or USER_ACTION_ROUTER_ADDRESS_{chain_id}"
        )
    return addr
