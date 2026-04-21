"""Append-only JSONL audit log for autopilot actions."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def new_run_id() -> str:
    return str(uuid.uuid4())


class AuditLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path

    def append(self, record: Dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log_event(
        self,
        *,
        run_id: str,
        action_type: str,
        status: str,
        chain_id: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        tx_hash: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self.append(
            {
                "ts": time.time(),
                "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "run_id": run_id,
                "action_type": action_type,
                "status": status,
                "chain_id": chain_id,
                "tx_hash": tx_hash,
                "error": error,
                "details": details or {},
            }
        )

    def tail(self, max_lines: int = 20) -> List[Dict[str, Any]]:
        if not self.log_path.is_file():
            return []
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        out: List[Dict[str, Any]] = []
        for line in lines[-max_lines:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
