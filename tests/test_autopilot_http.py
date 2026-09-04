"""Smoke tests for rubric_forecast.server.http."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from rubric_forecast.server.http import serve_threaded

_MINIMAL_FORECAST = {
    "question": "HTTP smoke?",
    "options": ["yes", "no"],
    "resolution_rule": "Test.",
    "evidence": [
        {
            "id": "e1",
            "claim": "c",
            "source": "t",
            "supports_option": "yes",
            "stance": "for",
            "strength": "medium",
            "dimension_scores": {
                "reliability": 0.8,
                "mechanism_fit": 0.7,
                "novelty": 0.6,
                "timeliness": 0.65,
            },
        }
    ],
}


def _post(url: str, body: dict, headers: Optional[Dict[str, str]] = None) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


@pytest.fixture()
def http_srv(monkeypatch, tmp_path):
    for key in (
        "PRIVATE_KEY",
        "AUTOPILOT_PRIVATE_KEY",
        "ETH_PRIVATE_KEY",
        "EXECUTOR_PRIVATE_KEY",
        "AUTOPILOT_RPC_URL",
        "RPC_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTOPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_DRY_RUN", "true")
    monkeypatch.setenv("AUTOPILOT_MIN_SCORE", "0.01")
    monkeypatch.setenv("LIFEFUN_API_BASE_URL", "https://api.example.invalid")
    monkeypatch.setenv("LIFEFUN_JWT", "test-jwt")
    monkeypatch.setenv("AUTOPILOT_CANDIDATES_FILE", str(Path(__file__).resolve().parents[1] / "examples" / "autopilot_candidates.json"))
    httpd, shutdown = serve_threaded("127.0.0.1", 0, webhook_token=None)
    _host, port = httpd.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        yield base
    finally:
        shutdown()


def test_api_forecast(http_srv: str) -> None:
    code, body = _post(f"{http_srv}/api/forecast", _MINIMAL_FORECAST)
    assert code == 200
    assert body.get("status") == "ok"


def test_api_forecast_with_bearer() -> None:
    httpd, shutdown = serve_threaded("127.0.0.1", 0, webhook_token="secret")
    _host, port = httpd.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        code, body = _post(
            f"{base}/api/forecast",
            _MINIMAL_FORECAST,
            headers={"Authorization": "Bearer secret"},
        )
        assert code == 200
        assert body.get("status") == "ok"
    finally:
        shutdown()


def test_api_autopilot_run_dry(http_srv: str) -> None:
    code, body = _post(f"{http_srv}/api/autopilot/run", {})
    assert code == 200
    assert int(body.get("executed", 0)) >= 1


def test_api_forecast_unauthorized_when_token_set(monkeypatch, tmp_path) -> None:
    for key in (
        "PRIVATE_KEY",
        "AUTOPILOT_PRIVATE_KEY",
        "ETH_PRIVATE_KEY",
        "EXECUTOR_PRIVATE_KEY",
        "AUTOPILOT_RPC_URL",
        "RPC_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTOPILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_DRY_RUN", "true")
    monkeypatch.setenv("LIFEFUN_API_BASE_URL", "https://api.example.invalid")
    monkeypatch.setenv("LIFEFUN_JWT", "test-jwt")
    httpd, shutdown = serve_threaded("127.0.0.1", 0, webhook_token="secret")
    _host, port = httpd.server_address
    base = f"http://127.0.0.1:{port}"
    try:
        code, _ = _post(f"{base}/api/forecast", _MINIMAL_FORECAST)
        assert code == 401
    finally:
        shutdown()
