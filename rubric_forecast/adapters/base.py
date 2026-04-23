"""Pluggable DApp adapter: discover candidates for autopilot cycles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, runtime_checkable

from rubric_forecast.config import AutopilotConfig
from rubric_forecast.openclaw_adapter import OpenClawAdapter


@dataclass
class AdapterContext:
    """Context passed to adapter discovery."""

    config: AutopilotConfig
    api: OpenClawAdapter


@runtime_checkable
class DAppAdapter(Protocol):
    """Discover autopilot candidates for a target dApp (e.g. LifeFun)."""

    name: str

    def discover_candidates(self, ctx: AdapterContext) -> List[Dict[str, Any]]:
        """Return rubric-shaped candidate dicts (question, options, evidence, meta)."""
        ...
