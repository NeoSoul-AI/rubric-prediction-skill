"""Encode + send UserActionRouter mintWithSig / intakeReasoning via TxExecutor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from rubric_forecast.adapters.lifefun import AgentMintWithSigPayload, ReasoningIntakeWithSigPayload
from rubric_forecast.executor.base import ExecutionResult, TxExecutor


def _abi_path() -> Path:
    return Path(__file__).resolve().parent.parent / "contracts" / "user_action_router.json"


def _load_router_abi() -> List[Any]:
    raw = json.loads(_abi_path().read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("user_action_router.json must be a JSON array")
    return raw


def _hex_to_bytes32(h: str) -> bytes:
    s = h.strip().lower().removeprefix("0x")
    if len(s) != 64:
        raise ValueError(f"expected 32-byte hex string, got len {len(s)}")
    return bytes.fromhex(s)


def _hex_to_bytes(h: str) -> bytes:
    s = h.strip().lower().removeprefix("0x")
    return bytes.fromhex(s)


class ContractWriter:
    """Build calldata for router functions and submit through a whitelisted TxExecutor."""

    def __init__(self, *, rpc_url: str, chain_id: int, executor: TxExecutor, abi: Optional[List[Any]] = None) -> None:
        self._rpc_url = rpc_url
        self._chain_id = chain_id
        self._executor = executor
        self._abi = abi or _load_router_abi()

    def _require_web3(self):  # noqa: ANN202
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware
        except ImportError as e:
            raise ImportError(
                "ContractWriter requires web3: pip install 'rubric-forecasting[autopilot]'"
            ) from e
        return Web3, ExtraDataToPOAMiddleware

    def _w3(self):  # noqa: ANN202
        Web3, ExtraDataToPOAMiddleware = self._require_web3()
        w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        try:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception:
            pass
        return w3

    def _encode_mint(self, payload: AgentMintWithSigPayload) -> str:
        Web3, _ = self._require_web3()
        w3 = self._w3()
        c = w3.eth.contract(
            address=Web3.to_checksum_address(payload.contract_address),
            abi=self._abi,
        )
        sig = payload.signature.strip()
        sig_b = _hex_to_bytes(sig) if sig.startswith("0x") else bytes.fromhex(sig)
        data = c.functions.mintWithSig(
            Web3.to_checksum_address(payload.to),
            _hex_to_bytes32(payload.profile_hash),
            payload.uri,
            int(payload.nonce),
            int(payload.deadline),
            sig_b,
        )._encode_transaction_data()
        return data if data.startswith("0x") else "0x" + data

    def _encode_intake(self, payload: ReasoningIntakeWithSigPayload) -> str:
        Web3, _ = self._require_web3()
        w3 = self._w3()
        c = w3.eth.contract(
            address=Web3.to_checksum_address(payload.contract_address),
            abi=self._abi,
        )
        sig = payload.signature.strip()
        sig_b = _hex_to_bytes(sig) if sig.startswith("0x") else bytes.fromhex(sig)
        data = c.functions.intakeReasoning(
            int(payload.token_id),
            int(payload.source_opinion_id),
            _hex_to_bytes32(payload.reasoning_hash),
            _hex_to_bytes32(payload.opinion_hash),
            _hex_to_bytes32(payload.new_memory_root),
            int(payload.nonce),
            int(payload.deadline),
            sig_b,
        )._encode_transaction_data()
        return data if data.startswith("0x") else "0x" + data

    def estimate_mint_gas(self, payload: AgentMintWithSigPayload) -> int:
        Web3, _ = self._require_web3()
        w3 = self._w3()
        data = self._encode_mint(payload)
        partial = {
            "from": self._executor.address,
            "to": Web3.to_checksum_address(payload.contract_address),
            "data": data,
            "value": 0,
        }
        return int(w3.eth.estimate_gas(partial))

    def send_mint_with_sig(self, payload: AgentMintWithSigPayload, *, gas_limit: Optional[int] = None) -> ExecutionResult:
        data = self._encode_mint(payload)
        return self._executor.send_contract_call(
            to=payload.contract_address,
            data_hex=data,
            value_wei=0,
            gas_limit=gas_limit,
            function_name="mintWithSig",
        )

    def estimate_intake_gas(self, payload: ReasoningIntakeWithSigPayload) -> int:
        Web3, _ = self._require_web3()
        w3 = self._w3()
        data = self._encode_intake(payload)
        partial = {
            "from": self._executor.address,
            "to": Web3.to_checksum_address(payload.contract_address),
            "data": data,
            "value": 0,
        }
        return int(w3.eth.estimate_gas(partial))

    def send_intake_reasoning(
        self, payload: ReasoningIntakeWithSigPayload, *, gas_limit: Optional[int] = None
    ) -> ExecutionResult:
        data = self._encode_intake(payload)
        return self._executor.send_contract_call(
            to=payload.contract_address,
            data_hex=data,
            value_wei=0,
            gas_limit=gas_limit,
            function_name="intakeReasoning",
        )
