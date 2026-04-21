"""Tests for OpenClawAdapter auth, error mapping, and Lifefun write paths."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from rubric_forecast.openclaw_adapter import AdapterApiError, OpenClawAdapter


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_jwt_and_openclaw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_auth: list[str | None] = []

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        seen_auth.append(req.headers.get("Authorization"))
        if req.full_url.endswith("/v1/auth/nonce"):
            return FakeResponse({"address": "0xabc", "nonce": "1", "message": "m"})
        if req.full_url.endswith("/v1/auth/login"):
            return FakeResponse({"token": "jwt-token", "expires_at": "2099-01-01"})
        if req.full_url.endswith("/v1/auth/heartbeat"):
            return FakeResponse({"status": "ok"})
        if req.full_url.endswith("/v1/openclaw/me"):
            return FakeResponse({"agent_id": 1})
        raise AssertionError(f"unexpected URL: {req.full_url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    adapter = OpenClawAdapter("https://api.example.com", api_key="oc-key")
    adapter.auth_nonce("0xabc")
    adapter.auth_login("0xabc", "1", "0xsig")
    adapter.auth_heartbeat()
    adapter.openclaw_me()

    assert seen_auth[0] is None
    assert seen_auth[1] is None
    assert seen_auth[2] == "Bearer jwt-token"
    assert seen_auth[3] == "Bearer oc-key"


def test_http_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"bad token"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = OpenClawAdapter("https://api.example.com", jwt_token="bad-token")

    with pytest.raises(AdapterApiError) as ei:
        adapter.feed_reference(source_opinion_id=11, target_agent_id=22)

    assert ei.value.status_code == 401
    assert "/v1/references/feed" in ei.value.path


def test_lifefun_write_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls: list[str] = []

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        seen_urls.append(req.full_url)
        return FakeResponse({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = OpenClawAdapter("https://api.example.com", jwt_token="jwt")

    adapter.feed_reference(source_opinion_id=1, target_agent_id=2, note="n")
    adapter.memory_from_opinion(agent_id=9, opinion_id=77, reasoning_hash="0x01")
    adapter.confirm_mint(agent_id=9, tx_hash="0xabc")
    adapter.rotate_openclaw_key(agent_id=9)

    assert any(u.endswith("/v1/references/feed") for u in seen_urls)
    assert any(u.endswith("/v1/agents/9/memories/from-opinion") for u in seen_urls)
    assert any(u.endswith("/v1/agents/9/mint") for u in seen_urls)
    assert any(u.endswith("/v1/agents/9/openclaw-key/rotate") for u in seen_urls)
