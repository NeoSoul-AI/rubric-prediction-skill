"""Abstract executor contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass
class ExecutionResult:
    ok: bool
    tx_hash: Optional[str] = None
    error: Optional[str] = None
    receipt: Optional[dict[str, Any]] = None


class TxExecutor(Protocol):
    """Sign and broadcast transactions."""

    chain_id: int

    @property
    def address(self) -> str:
        """EOA address used for gas estimation and signing."""
        ...

    def send_contract_call(
        self,
        *,
        to: str,
        data_hex: str,
        value_wei: int = 0,
        gas_limit: Optional[int] = None,
        function_name: Optional[str] = None,
    ) -> ExecutionResult:
        ...
