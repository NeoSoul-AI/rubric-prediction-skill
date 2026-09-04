"""EOA signing via eth_account + web3 JSON-RPC (optional autopilot deps)."""

from __future__ import annotations

from typing import Any, Optional

from rubric_forecast.executor.base import ExecutionResult


class EoaExecutor:
    """Sign and send raw transactions using a local private key account."""

    def __init__(
        self,
        *,
        rpc_url: str,
        chain_id: int,
        account: Any,
    ) -> None:
        self.rpc_url = rpc_url
        self._chain_id = chain_id
        self._account = account

    @property
    def chain_id(self) -> int:
        return self._chain_id

    @property
    def address(self) -> str:
        return str(self._account.address)

    def _require_web3(self):
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware
        except ImportError as e:
            raise ImportError(
                "EoaExecutor requires web3: pip install 'rubric-forecasting[autopilot]'"
            ) from e
        return Web3, ExtraDataToPOAMiddleware

    def _w3(self):
        Web3, ExtraDataToPOAMiddleware = self._require_web3()
        w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        try:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception:
            pass
        return w3

    def send_contract_call(
        self,
        *,
        to: str,
        data_hex: str,
        value_wei: int = 0,
        gas_limit: Optional[int] = None,
        function_name: Optional[str] = None,
    ) -> ExecutionResult:
        del function_name  # EOA path ignores; SessionKeyExecutor uses it
        try:
            Web3, _ = self._require_web3()
            w3 = self._w3()
            if int(w3.eth.chain_id) != int(self._chain_id):
                return ExecutionResult(
                    ok=False,
                    error=f"RPC chain_id {w3.eth.chain_id} != configured {self._chain_id}",
                )
            nonce = w3.eth.get_transaction_count(self._account.address)
            data = data_hex if data_hex.startswith("0x") else "0x" + data_hex
            tx_partial = {
                "from": self._account.address,
                "to": Web3.to_checksum_address(to),
                "data": data,
                "value": value_wei,
            }
            gas = gas_limit
            if gas is None:
                gas = min(500_000, int(w3.eth.estimate_gas(tx_partial)))
            tx = {
                "to": Web3.to_checksum_address(to),
                "value": value_wei,
                "gas": gas,
                "gasPrice": int(w3.eth.gas_price),
                "nonce": nonce,
                "chainId": int(self._chain_id),
                "data": data,
            }
            signed = self._account.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or getattr(
                signed, "rawTransaction", None
            )
            if raw is None:
                return ExecutionResult(ok=False, error="sign_transaction returned no raw bytes")
            tx_hash = w3.eth.send_raw_transaction(raw).hex()
            receipt = dict(w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120))
            status_ok = receipt.get("status") == 1
            return ExecutionResult(
                ok=status_ok,
                tx_hash=tx_hash,
                receipt=receipt,
                error=None if status_ok else "transaction reverted",
            )
        except Exception as e:
            return ExecutionResult(ok=False, error=str(e))
