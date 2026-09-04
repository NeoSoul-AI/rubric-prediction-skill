"""Action handler context and result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from rubric_forecast.daemon import AutopilotDaemon
    from rubric_forecast.executor.contract_writer import ContractWriter


@dataclass
class ActionResult:
    ok: bool
    gas_used: int = 0
    error: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


@dataclass
class ActionContext:
    daemon: AutopilotDaemon
    candidate: Dict[str, Any]
    cid: str
    run_id: str
    top_score: float
    final_answer: Any
    executor: Optional[Any]
    contract_writer: Optional[ContractWriter]
