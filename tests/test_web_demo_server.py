"""Smoke tests for examples/web_demo/server.py (stdlib HTTP demo)."""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVER_PATH = _REPO_ROOT / "examples" / "web_demo" / "server.py"

_MINIMAL_FORECAST = {
    "question": "Smoke test question?",
    "options": ["yes", "no"],
    "resolution_rule": "Test resolution.",
    "evidence": [
        {
            "id": "e1",
            "claim": "Supporting claim",
            "source": "test",
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


def _load_server_module():
    spec = importlib.util.spec_from_file_location("web_demo_server", _SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def demo_httpd():
    mod = _load_server_module()
    httpd = mod.ThreadingHTTPServer(("127.0.0.1", 0), mod.DemoHandler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _get(path: str, httpd) -> tuple[int, bytes]:
    _host, port = httpd.server_address
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, resp.read()


def _post_json(path: str, httpd, payload: dict) -> tuple[int, dict]:
    _host, port = httpd.server_address
    url = f"http://127.0.0.1:{port}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, json.loads(raw)


def test_get_index_and_sample(demo_httpd):
    st, data = _get("/", demo_httpd)
    assert st == 200
    assert b"Rubric Forecast" in data

    st2, raw2 = _get("/sample-input", demo_httpd)
    assert st2 == 200
    sample = json.loads(raw2.decode("utf-8"))
    assert "question" in sample
    assert "options" in sample

    st3, raw3 = _get("/lifefun-candidates", demo_httpd)
    assert st3 == 200
    candidates = json.loads(raw3.decode("utf-8"))
    assert isinstance(candidates, list)
    assert len(candidates) >= 1


def test_get_autopilot_env_example(demo_httpd):
    st, raw = _get("/autopilot-env-example", demo_httpd)
    assert st == 200
    text = raw.decode("utf-8")
    assert "AUTOPILOT_DRY_RUN" in text
    assert "autopilot_candidates.json" in text or "AUTOPILOT_CANDIDATES_FILE" in text


def test_post_forecast_minimal(demo_httpd):
    st, out = _post_json("/api/forecast", demo_httpd, _MINIMAL_FORECAST)
    assert st == 200
    assert out.get("status") == "ok"
    assert "engine" in out
    assert out.get("final_answer") in ("yes", "no")


def test_post_unknown_path_404(demo_httpd):
    _host, port = demo_httpd.server_address
    url = f"http://127.0.0.1:{port}/nope"
    req = urllib.request.Request(url, method="POST", data=b"{}")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected request to fail for unknown path")
    except urllib.error.HTTPError as exc_info:
        assert exc_info.code == 404
    except ConnectionAbortedError:
        # Windows may abort socket on BaseHTTPRequestHandler.send_error for some POST cases.
        pass

