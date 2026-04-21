"""HTTP client for OpenClaw / LifeFun APIs with JWT + OpenClaw auth."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

AuthMode = Literal["none", "jwt", "openclaw"]


@dataclass
class AdapterApiError(RuntimeError):
    """Uniform API error model for retry / classification in daemon."""

    status_code: int
    path: str
    body: str

    def __str__(self) -> str:
        return f"HTTP {self.status_code} {self.path}: {self.body}"


class OpenClawAdapter:
    """
    Minimal REST adapter. Base URL should include scheme, e.g. https://api.example.com
    Paths follow common LifeFun-style routes; override via env or subclass.
    """

    def __init__(
        self,
        base_url: Optional[str],
        api_key: Optional[str] = None,
        jwt_token: Optional[str] = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.timeout_seconds = timeout_seconds

    def set_jwt(self, token: Optional[str]) -> None:
        self.jwt_token = token

    def _headers(self, auth: AuthMode = "none") -> Dict[str, str]:
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth == "jwt" and self.jwt_token:
            h["Authorization"] = f"Bearer {self.jwt_token}"
        if auth == "openclaw" and self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        auth: AuthMode = "none",
    ) -> Any:
        if not self.base_url:
            raise RuntimeError("OPENCLAW_BASE_URL / LIFEFUN_API_BASE_URL is not set")
        if auth == "jwt" and not self.jwt_token:
            raise RuntimeError("JWT auth requested but token is missing")
        if auth == "openclaw" and not self.api_key:
            raise RuntimeError("OpenClaw auth requested but API key is missing")
        url = f"{self.base_url}{path}"
        if params:
            q = urllib.parse.urlencode(
                {k: params[k] for k in sorted(params) if params[k] is not None}
            )
            if q:
                url = f"{url}?{q}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=self._headers(auth=auth),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise AdapterApiError(int(e.code), path, err_body) from e

    def health_ping(self) -> bool:
        """GET /health or HEAD base — best effort."""
        if not self.base_url:
            return False
        for path in ("/health", "/v1/health", ""):
            try:
                url = f"{self.base_url}{path}" if path else self.base_url
                req = urllib.request.Request(url, method="GET", headers=self._headers())
                with urllib.request.urlopen(req, timeout=min(10.0, self.timeout_seconds)):
                    return True
            except OSError:
                continue
        return False

    # ---- Auth (lifefun /v1/auth/*) ----
    def auth_nonce(self, address: str) -> Any:
        return self._request(
            "POST",
            "/v1/auth/nonce",
            body={"address": address},
            auth="none",
        )

    def auth_login(self, address: str, nonce: str, signature: str) -> Any:
        result = self._request(
            "POST",
            "/v1/auth/login",
            body={"address": address, "nonce": nonce, "signature": signature},
            auth="none",
        )
        if isinstance(result, dict) and isinstance(result.get("token"), str):
            self.jwt_token = result["token"]
        return result

    def auth_heartbeat(self) -> Any:
        return self._request("POST", "/v1/auth/heartbeat", body={}, auth="jwt")

    def list_predictions(
        self,
        *,
        status: Optional[str] = None,
        chain_id: Optional[int] = None,
        limit: int = 20,
    ) -> Any:
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if chain_id is not None:
            params["chain_id"] = chain_id
        return self._request("GET", "/v1/predictions", params=params, auth="none")

    def prediction_detail(
        self,
        prediction_id: int | str,
        *,
        chain_id: Optional[int] = None,
    ) -> Any:
        params: Dict[str, Any] = {}
        if chain_id is not None:
            params["chain_id"] = chain_id
        return self._request(
            "GET",
            f"/v1/predictions/{prediction_id}",
            params=params if params else None,
            auth="none",
        )

    def get_feeding(
        self,
        *,
        tab: str = "recommended",
        chain_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        limit: int = 20,
    ) -> Any:
        params: Dict[str, Any] = {"tab": tab, "limit": limit}
        if chain_id is not None:
            params["chain_id"] = chain_id
        if agent_id is not None:
            params["agent_id"] = agent_id
        return self._request("GET", "/v1/platform/feeding", params=params, auth="none")

    # ---- Write APIs (JWT required) ----
    def feed_reference(
        self,
        *,
        source_opinion_id: int,
        target_agent_id: int,
        note: Optional[str] = None,
        tx_hash: Optional[str] = None,
    ) -> Any:
        body: Dict[str, Any] = {
            "source_opinion_id": source_opinion_id,
            "target_agent_id": target_agent_id,
        }
        if note:
            body["note"] = note
        if tx_hash:
            body["tx_hash"] = tx_hash
        return self._request("POST", "/v1/references/feed", body=body, auth="jwt")

    def memory_from_opinion(
        self,
        *,
        agent_id: int | str,
        opinion_id: int,
        reasoning_hash: Optional[str] = None,
        tx_hash: Optional[str] = None,
    ) -> Any:
        body: Dict[str, Any] = {"opinion_id": opinion_id}
        if reasoning_hash:
            body["reasoning_hash"] = reasoning_hash
        if tx_hash:
            body["tx_hash"] = tx_hash
        return self._request(
            "POST",
            f"/v1/agents/{agent_id}/memories/from-opinion",
            body=body,
            auth="jwt",
        )

    def confirm_mint(self, *, agent_id: int | str, tx_hash: str) -> Any:
        return self._request(
            "POST",
            f"/v1/agents/{agent_id}/mint",
            body={"tx_hash": tx_hash},
            auth="jwt",
        )

    def rotate_openclaw_key(self, *, agent_id: int | str) -> Any:
        return self._request(
            "POST",
            f"/v1/agents/{agent_id}/openclaw-key/rotate",
            body={},
            auth="jwt",
        )

    # ---- OpenClaw key only ----
    def openclaw_me(self) -> Any:
        """GET /v1/openclaw/me with OpenClaw API key as Bearer."""
        return self._request("GET", "/v1/openclaw/me", auth="openclaw")


def predictions_to_candidate_payloads(raw: Any) -> List[Dict[str, Any]]:
    """
    Best-effort map API list response to rubric payloads.
    If API shape differs, users can use AUTOPILOT_CANDIDATES_FILE instead.
    """
    items: List[Any]
    if isinstance(raw, dict) and "items" in raw:
        items = list(raw["items"])
    elif isinstance(raw, list):
        items = list(raw)
    else:
        return []

    out: List[Dict[str, Any]] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        q = it.get("title") or it.get("question") or f"prediction-{i}"
        options = it.get("options") or ["yes", "no"]
        if not isinstance(options, list):
            options = ["yes", "no"]
        resolution = it.get("resolution_rule") or "Resolved per platform rules."
        evidence = it.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            evidence = [
                {
                    "id": "e1",
                    "claim": "Synthetic placeholder — replace with real evidence.",
                    "source": "openclaw-adapter",
                    "supports_option": options[0] if options else "yes",
                    "stance": "for",
                    "strength": "weak",
                    "dimension_scores": {
                        "reliability": 0.5,
                        "mechanism_fit": 0.5,
                        "novelty": 0.5,
                        "timeliness": 0.5,
                    },
                }
            ]
        out.append(
            {
                "meta": {"id": it.get("id", i), "source": "predictions_api"},
                "question": str(q),
                "options": [str(x) for x in options],
                "resolution_rule": str(resolution),
                "evidence": evidence,
            }
        )
    return out
