"""Risk policy: limits, whitelists, cooldown, circuit breaker."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


DEFAULT_POLICY: Dict[str, Any] = {
    "enabled": True,
    "emergency_pause": False,
    "chain_whitelist": [56, 97, 1, 137],
    "contract_whitelist": [],
    "function_whitelist": [
        "generic",
        "mintWithSig",
        "intakeReasoning",
        "judge",
        "transfer",
    ],
    "limits": {
        "single_tx_max_gas": 500_000,
        "single_tx_max_gas_by_action": {
            "predict_only": 21_000,
            "feed_reference": 21_000,
            "rotate_openclaw_key": 21_000,
            "mint_confirm": 500_000,
            "adopt": 500_000,
        },
        "daily_gas_budget_units": 5_000_000_000,
        "daily_action_limit": 100,
        "daily_action_limit_by_type": {
            "mint": 10,
            "mint_confirm": 10,
            "adopt": 40,
            "predict_only": 200,
            "rotate_openclaw_key": 50,
            "endorse": 40,
            "feed_reference": 50,
            "generic": 100,
        },
    },
    "function_selector_whitelist": [],
    "cooldown": {
        "by_agent_seconds": 120,
        "by_topic_seconds": 300,
    },
    "circuit_breaker": {
        "max_consecutive_failures": 5,
        "recovery_wait_seconds": 1800,
    },
}


@dataclass
class PolicyCheckResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


class PolicyEngine:
    """Load policy JSON and evaluate actions against in-memory counters."""

    def __init__(self, policy_path: Path, state_path: Path) -> None:
        self.policy_path = policy_path
        self.state_path = state_path
        self._policy: Dict[str, Any] = dict(DEFAULT_POLICY)
        self._state: Dict[str, Any] = {
            "consecutive_failures": 0,
            "halted_until": None,
            "daily_date": "",
            "daily_action_count": 0,
            "daily_gas_used": 0,
            "action_counts_by_type": {},
            "last_action_at_by_key": {},
        }
        self.reload_policy()
        self.load_state()

    def reload_policy(self) -> None:
        if self.policy_path.is_file():
            data = json.loads(self.policy_path.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_POLICY)
            merged.update(data)
            self._policy = merged

    def get_function_whitelist(self) -> Set[str]:
        return {str(x) for x in (self._policy.get("function_whitelist") or [])}

    def load_state(self) -> None:
        if self.state_path.is_file():
            try:
                self._state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
        self._rollover_daily_if_needed()

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def _rollover_daily_if_needed(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self._state.get("daily_date") != today:
            self._state["daily_date"] = today
            self._state["daily_action_count"] = 0
            self._state["daily_gas_used"] = 0
            self._state["action_counts_by_type"] = {}

    def is_halted(self) -> bool:
        hu = self._state.get("halted_until")
        if hu is None:
            return False
        if time.time() < float(hu):
            return True
        self._state["halted_until"] = None
        self._state["consecutive_failures"] = 0
        self.save_state()
        return False

    def record_success(self) -> None:
        self._state["consecutive_failures"] = 0
        self.save_state()

    def record_failure(self) -> None:
        self._state["consecutive_failures"] = int(self._state.get("consecutive_failures", 0)) + 1
        max_f = int(self._policy.get("circuit_breaker", {}).get("max_consecutive_failures", 5))
        wait = int(self._policy.get("circuit_breaker", {}).get("recovery_wait_seconds", 1800))
        if self._state["consecutive_failures"] >= max_f:
            self._state["halted_until"] = time.time() + wait
        self.save_state()

    def check_action(
        self,
        *,
        chain_id: int,
        action_type: str,
        to_address: Optional[str] = None,
        function_name: Optional[str] = None,
        estimated_gas: int = 21000,
        dedup_key: Optional[str] = None,
        calldata_prefix: Optional[str] = None,
    ) -> PolicyCheckResult:
        reasons: List[str] = []
        if not self._policy.get("enabled", True):
            return PolicyCheckResult(False, ["policy disabled"])

        if self._policy.get("emergency_pause"):
            return PolicyCheckResult(False, ["emergency_pause in policy.json"])

        if self.is_halted():
            return PolicyCheckResult(False, ["circuit breaker halted"])

        chains: Set[int] = set(self._policy.get("chain_whitelist") or [])
        if chains and chain_id not in chains:
            reasons.append(f"chain_id {chain_id} not in whitelist")

        fn = function_name or "generic"
        fw: List[str] = list(self._policy.get("function_whitelist") or [])
        if fw and fn not in fw:
            reasons.append(f"function {fn} not in function_whitelist")

        sel_wl: List[str] = list(self._policy.get("function_selector_whitelist") or [])
        if sel_wl and calldata_prefix:
            norm = calldata_prefix.strip().lower()
            allowed = {str(s).strip().lower() for s in sel_wl}
            if norm not in allowed:
                reasons.append("calldata function selector not in function_selector_whitelist")

        contracts: List[str] = [c.lower() for c in (self._policy.get("contract_whitelist") or [])]
        if contracts and to_address:
            if to_address.lower() not in contracts:
                reasons.append("contract not in contract_whitelist")

        self._rollover_daily_if_needed()
        limits = self._policy.get("limits", {})
        daily_limit = int(limits.get("daily_action_limit", 10_000))
        if int(self._state.get("daily_action_count", 0)) >= daily_limit:
            reasons.append("daily_action_limit exceeded")

        by_type = limits.get("daily_action_limit_by_type") or {}
        cap = by_type.get(action_type) or by_type.get("generic")
        if cap is not None:
            counts: Dict[str, int] = dict(self._state.get("action_counts_by_type") or {})
            if int(counts.get(action_type, 0)) >= int(cap):
                reasons.append(f"daily limit for action type {action_type}")

        by_gas = limits.get("single_tx_max_gas_by_action") or {}
        base_gas_cap = int(limits.get("single_tx_max_gas", 10_000_000))
        if action_type in by_gas:
            max_gas = min(int(by_gas[action_type]), base_gas_cap)
        else:
            max_gas = base_gas_cap
        if estimated_gas > max_gas:
            reasons.append(f"estimated_gas {estimated_gas} > cap {max_gas} for action {action_type}")

        budget = int(limits.get("daily_gas_budget_units", 10**18))
        if int(self._state.get("daily_gas_used", 0)) + estimated_gas > budget:
            reasons.append("daily_gas_budget would be exceeded")

        if dedup_key:
            cooldown = self._policy.get("cooldown", {})
            now = time.time()
            last_map: Dict[str, float] = dict(self._state.get("last_action_at_by_key") or {})
            sec = float(cooldown.get("by_agent_seconds", 0))
            if sec > 0 and dedup_key in last_map:
                if now - float(last_map[dedup_key]) < sec:
                    reasons.append("cooldown not elapsed for key")

        return PolicyCheckResult(len(reasons) == 0, reasons)

    def record_action_committed(
        self,
        action_type: str,
        gas_used: int,
        dedup_key: Optional[str] = None,
    ) -> None:
        self._rollover_daily_if_needed()
        self._state["daily_action_count"] = int(self._state.get("daily_action_count", 0)) + 1
        self._state["daily_gas_used"] = int(self._state.get("daily_gas_used", 0)) + int(gas_used)
        counts: Dict[str, int] = dict(self._state.get("action_counts_by_type") or {})
        counts[action_type] = int(counts.get(action_type, 0)) + 1
        self._state["action_counts_by_type"] = counts
        if dedup_key:
            last = dict(self._state.get("last_action_at_by_key") or {})
            last[dedup_key] = time.time()
            self._state["last_action_at_by_key"] = last
        self.save_state()


def write_default_policy_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_POLICY, indent=2), encoding="utf-8")
