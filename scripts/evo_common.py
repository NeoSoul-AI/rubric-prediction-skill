#!/usr/bin/env python3
"""Shared helpers for EvoEvo OpenClaw pipeline."""

from __future__ import annotations

import json
import os
import random
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) OpenClaw-EvoPipeline/1.0"


def skill_root() -> Path:
    """Directory containing scripts/ (rubric-prediction-skill root)."""
    return Path(__file__).resolve().parent.parent


def load_json_path(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = config_path or (skill_root() / "config" / "evo_config.json")
    return load_json_path(cfg_path)


def http_request_json(
    url: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    retries: int = 0,
    retry_backoff_seconds: float = 1.5,
    retry_for_statuses: Optional[Tuple[int, ...]] = None,
) -> Tuple[int, Any]:
    """Return (status, parsed_json_or_text), with optional retry/backoff."""
    retry_for = retry_for_statuses or (429, 500, 502, 503, 504)
    attempt = 0
    while True:
        h = {
            "User-Agent": os.environ.get("EVO_HTTP_USER_AGENT", DEFAULT_UA),
            "Accept": "application/json",
        }
        if headers:
            h.update(headers)
        body: Optional[bytes] = None
        if data is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            status = e.code
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as e:
            if attempt < retries:
                delay = retry_backoff_seconds * (2**attempt) + random.uniform(0, 0.5)
                time.sleep(delay)
                attempt += 1
                continue
            raise RuntimeError(f"HTTP request failed after retries: {e}") from e

        try:
            payload = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            payload = raw

        if status in retry_for and attempt < retries:
            delay = retry_backoff_seconds * (2**attempt) + random.uniform(0, 0.5)
            time.sleep(delay)
            attempt += 1
            continue
        return status, payload


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
