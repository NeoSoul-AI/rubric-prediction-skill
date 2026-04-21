"""Session-key / AA compatibility layer: function whitelist then delegate to inner executor."""

from __future__ import annotations

from typing import Any, Optional, Set

from rubric_forecast.executor.base import ExecutionResult, TxExecutor


class SessionKeyExecutor:
    """
    Enforces an allowed function-name set before sending.

    When `bundler_url` is set, ERC-4337 UserOperation submission can be added later;
    currently all sends go through `inner` (typically EoaExecutor) after whitelist check.
    """

    def __init__(
        self,
        inner: TxExecutor,
        *,
        allowed_functions: Set[str],
        bundler_url: Optional[str] = None,
    ) -> None:
        self._inner = inner
        self._allowed = frozenset(allowed_functions)
        self.bundler_url = bundler_url

    @property
    def chain_id(self) -> int:
        return int(self._inner.chain_id)

    def send_contract_call(
        self,
        *,
        to: str,
        data_hex: str,
        value_wei: int = 0,
        gas_limit: Optional[int] = None,
        function_name: Optional[str] = None,
    ) -> ExecutionResult:
        fn = function_name or "generic"
        if fn not in self._allowed:
            return ExecutionResult(
                ok=False,
                error=f"function '{fn}' not in session allowlist: {sorted(self._allowed)}",
            )
        if self.bundler_url:
            # Placeholder: AA path would build UserOperation and POST to bundler.
            return ExecutionResult(
                ok=False,
                error="bundler_url set but ERC-4337 UserOperation path not implemented; "
                "clear AUTOPILOT_BUNDLER_URL or use execution_mode=eoa",
            )
        return self._inner.send_contract_call(
            to=to,
            data_hex=data_hex,
            value_wei=value_wei,
            gas_limit=gas_limit,
            function_name=function_name,
        )
