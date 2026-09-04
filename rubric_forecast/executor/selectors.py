"""First 4-byte selectors (0x + 8 hex) for policy checks."""

from __future__ import annotations

from typing import Optional

# keccak256(signature)[:4]
SELECTOR_PREFIX: dict[str, str] = {
    "mintWithSig": "0xa63d55a7",
    "intakeReasoning": "0x4ed1f275",
}


def selector_prefix(function_name: str) -> Optional[str]:
    return SELECTOR_PREFIX.get(function_name)
