"""Optional stdlib HTTP server: POST /api/forecast and POST /api/autopilot/run."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional, Tuple
from urllib.parse import urlparse

from rubric_forecast.config import AutopilotConfig, forbid_plaintext_private_key_in_env
from rubric_forecast.daemon import AutopilotDaemon, ensure_policy_file
from rubric_forecast.engine import run_forecast


def _parse_bearer(headers: dict[str, str]) -> Optional[str]:
    auth = headers.get("Authorization") or headers.get("authorization")
    if not auth:
        return None
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def _read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b""
    if not raw.strip():
        return {}
    return json.loads(raw.decode("utf-8"))


def make_handler_class(webhook_token: Optional[str]) -> type[BaseHTTPRequestHandler]:
    class AutopilotHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _check_auth(self) -> bool:
            if not webhook_token:
                return True
            tok = _parse_bearer(dict(self.headers))
            return bool(tok and tok == webhook_token)

        def _send_json(self, code: int, body: Any) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path or "/"
            if not self._check_auth():
                self._send_json(401, {"status": "error", "message": "unauthorized webhook token"})
                return
            try:
                req_body = _read_json_body(self)
            except json.JSONDecodeError:
                self._send_json(400, {"status": "error", "message": "invalid JSON"})
                return

            if path == "/api/forecast":
                try:
                    out = run_forecast(req_body if isinstance(req_body, dict) else {})
                except Exception as e:  # noqa: BLE001
                    self._send_json(500, {"status": "error", "message": str(e)})
                    return
                code = 200 if out.get("status") == "ok" else 422
                self._send_json(code, out)
                return

            if path == "/api/autopilot/run":
                try:
                    forbid_plaintext_private_key_in_env()
                    cfg = AutopilotConfig.from_env()
                    ensure_policy_file(cfg.policy_path)
                    pw = os.environ.get("AUTOPILOT_KEYSTORE_PASSWORD", "")
                    cand_raw = None
                    if isinstance(req_body, list):
                        cand_raw = req_body
                    elif isinstance(req_body, dict) and isinstance(req_body.get("candidates"), list):
                        cand_raw = req_body["candidates"]
                    daemon = AutopilotDaemon(cfg)
                    summary = daemon.run_once(
                        keystore_password=pw,
                        candidates_override=cand_raw,
                    )
                except Exception as e:  # noqa: BLE001
                    self._send_json(500, {"status": "error", "message": str(e)})
                    return
                self._send_json(200, summary)
                return

            self._send_json(404, {"status": "error", "message": "not found"})

    return AutopilotHTTPHandler


def serve(host: str, port: int, *, webhook_token: Optional[str] = None) -> None:
    handler = make_handler_class(webhook_token)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.serve_forever()


def serve_threaded(
    host: str, port: int, *, webhook_token: Optional[str] = None
) -> Tuple[ThreadingHTTPServer, Callable[[], None]]:
    """Start server; return (httpd, shutdown_fn) for tests."""
    handler = make_handler_class(webhook_token)
    httpd = ThreadingHTTPServer((host, port), handler)

    def shutdown() -> None:
        httpd.shutdown()

    import threading

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, shutdown
