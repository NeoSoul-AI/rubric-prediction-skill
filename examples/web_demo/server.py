#!/usr/bin/env python3
"""
Local demo HTTP server for the rubric forecast engine (stdlib only).

From repository root:
  python examples/web_demo/server.py

Then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rubric_forecast.engine import run_forecast

_WEB_DIR = Path(__file__).resolve().parent
_SAMPLE_INPUT = _REPO_ROOT / "examples" / "polymarket_hormuz_geopolitics_input.json"
_HOST = "127.0.0.1"
_PORT = 8765


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class DemoHandler(BaseHTTPRequestHandler):
    """Serves the static demo page and POST /api/forecast."""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path in ("/", "/index.html"):
            index = _WEB_DIR / "index.html"
            data = index.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/sample-input":
            if not _SAMPLE_INPUT.is_file():
                _send_json(
                    self,
                    HTTPStatus.NOT_FOUND,
                    {"error": "sample file missing", "path": str(_SAMPLE_INPUT)},
                )
                return
            try:
                payload = json.loads(_SAMPLE_INPUT.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                _send_json(
                    self,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "failed to read sample", "detail": str(exc)},
                )
                return
            _send_json(self, HTTPStatus.OK, payload)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/api/forecast":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        length_raw = self.headers.get("Content-Length", "0")
        try:
            length = int(length_raw)
        except ValueError:
            _send_json(self, HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _send_json(
                self,
                HTTPStatus.BAD_REQUEST,
                {"error": "body must be JSON object", "detail": str(exc)},
            )
            return

        result = run_forecast(payload)
        _send_json(self, HTTPStatus.OK, result)


def main() -> int:
    server = ThreadingHTTPServer((_HOST, _PORT), DemoHandler)
    print(f"Rubric forecast demo: http://{_HOST}:{_PORT}/", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
