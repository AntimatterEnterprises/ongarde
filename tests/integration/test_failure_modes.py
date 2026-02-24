"""Integration tests for E-001-S-006: HTTP 502/400 failure mode separation.

Tests the proxy handler's failure mode handling at the integration level:
  - scan_or_block() stub wired into proxy_handler() (called before upstream)
  - Action.BLOCK → HTTP 400 with X-OnGarde-Block: true (upstream NEVER called)
  - httpx.ConnectError → HTTP 502 (NO X-OnGarde-Block header)
  - httpx.TimeoutException → HTTP 502 (NO X-OnGarde-Block header)
  - httpx.RemoteProtocolError → HTTP 502 (NO X-OnGarde-Block header)
  - httpx.InvalidURL → HTTP 500
  - Upstream HTTP 4xx/5xx → passed through as-is (NOT 502)
  - ALLOW → upstream connection established; upstream receives request

AC coverage:
  AC-E001-06 items 1–15 (failure mode taxonomy)
  DoD: all failure mode items
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.models.scan import Action, RiskLevel, ScanResult

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _stub_config() -> Config:
    return Config.defaults()


def _patch_load_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.main.load_config", lambda: _stub_config())


class _MockUpstream:
    """Mock upstream that records received requests and returns a configurable response."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: bytes = b'{"result": "mocked"}',
        content_type: str = "application/json",
        raise_on_send: Exception | None = None,
    ) -> None:
        self.received_requests: list[httpx.Request] = []
        self._status_code = status_code
        self._body = body
        self._content_type = content_type
        self._raise_on_send = raise_on_send

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.received_requests.append(request)
        if self._raise_on_send is not None:
            raise self._raise_on_send
        return httpx.Response(
            self._status_code,
            content=self._body,
            headers={"content-type": self._content_type},
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport)

    @property
    def request_count(self) -> int:
        return len(self.received_requests)


def _build_test_app(mock_upstream: _MockUpstream, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a ready OnGarde app wired to a mock upstream."""
    _patch_load_config(monkeypatch)
    application = create_app()
    monkeypatch.setattr(
        "app.main.create_http_client",
        lambda: mock_upstream.client(),
    )
    return application


def _make_request(
    client: TestClient,
    path: str = "/v1/chat/completions",
    body: bytes = b'{"model":"gpt-4","messages":[]}',
) -> Any:
    return client.post(
        path,
        content=body,
        headers={"content-type": "application/json"},
    )


# ─── Scan gate wiring: ALLOW path ─────────────────────────────────────────────


class TestScanGateAllowPath:
    """scan_or_block() stub returns ALLOW → proxy forwards to upstream."""

    def test_allow_result_reaches_upstream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When stub returns ALLOW, upstream receives the request."""
        upstream = _MockUpstream(status_code=200)
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 200
        # Upstream received the request (ALLOW → forwarded)
        assert upstream.request_count == 1

    def test_allow_response_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ALLOW path: upstream response body/status passed through unchanged."""
        upstream = _MockUpstream(status_code=200, body=b'{"choices":[]}')
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 200
        assert response.json() == {"choices": []}


# ─── Scan gate wiring: BLOCK path ─────────────────────────────────────────────


class TestScanGateBlockPath:
    """When scan_or_block() returns Action.BLOCK, proxy returns 400 and never calls upstream."""

    def test_block_returns_http_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06: Action.BLOCK → HTTP 400."""
        upstream = _MockUpstream(status_code=200)

        block_result = ScanResult(
            action=Action.BLOCK,
            scan_id="01BLOCKED000000000000000000",
            rule_id="TEST-RULE",
            risk_level=RiskLevel.CRITICAL,
        )
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                response = _make_request(client)

        assert response.status_code == 400

    def test_block_has_x_ongarde_block_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 2: BLOCK response MUST include X-OnGarde-Block: true."""
        upstream = _MockUpstream(status_code=200)

        block_result = ScanResult(
            action=Action.BLOCK,
            scan_id="01BLOCKED000000000000000000",
        )
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                response = _make_request(client)

        assert response.headers.get("x-ongarde-block") == "true"

    def test_block_has_x_ongarde_scan_id_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 2: BLOCK response MUST include X-OnGarde-Scan-ID."""
        scan_id = "01BLOCKED000000000000000000"
        upstream = _MockUpstream(status_code=200)

        block_result = ScanResult(action=Action.BLOCK, scan_id=scan_id)
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                response = _make_request(client)

        assert response.headers.get("x-ongarde-scan-id") == scan_id

    def test_block_upstream_never_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06: When BLOCK fires, upstream connection is NEVER opened."""
        upstream = _MockUpstream(status_code=200)

        block_result = ScanResult(action=Action.BLOCK, scan_id="01BLOCKED000000000000000000")
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                response = _make_request(client)

        assert response.status_code == 400
        # Upstream mock received zero requests — connection never opened
        assert upstream.request_count == 0

    def test_block_response_body_schema(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 8: BLOCK response body follows OpenAI-compatible schema."""
        upstream = _MockUpstream(status_code=200)

        block_result = ScanResult(
            action=Action.BLOCK,
            scan_id="01BLOCKED000000000000000000",
            rule_id="CRED-001",
            risk_level=RiskLevel.HIGH,
        )
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                response = _make_request(client)

        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "policy_violation"
        assert "ongarde" in body
        assert body["ongarde"]["blocked"] is True
        assert body["ongarde"]["rule_id"] == "CRED-001"

    def test_block_body_no_credentials_leaked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No raw credentials or internal scan details leaked in BLOCK response body."""
        upstream = _MockUpstream(status_code=200)

        # The content below represents what was in the request — it should NOT appear
        # in the response body (no echoing of request content).
        sensitive_content = "sk-proj-SuperSecretKey1234567890ABCDEF"
        block_result = ScanResult(
            action=Action.BLOCK,
            scan_id="01BLOCKED000000000000000000",
            redacted_excerpt="sk-***REDACTED***",  # sanitised form only
        )
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                response = client.post(
                    "/v1/chat/completions",
                    content=sensitive_content.encode(),
                    headers={"content-type": "application/json"},
                )

        # The raw sensitive content must not appear in the response body
        response_text = response.text
        assert sensitive_content not in response_text


# ─── Upstream connectivity failures → 502 ────────────────────────────────────


class TestConnectivityFailures:
    """AC-E001-06: httpx connectivity exceptions → HTTP 502, NO X-OnGarde-Block."""

    def test_connect_error_returns_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 11: httpx.ConnectError → HTTP 502."""
        upstream = _MockUpstream(raise_on_send=httpx.ConnectError("Connection refused"))
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 502

    def test_connect_error_no_x_ongarde_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 3: 502 on ConnectError MUST NOT have X-OnGarde-Block header."""
        upstream = _MockUpstream(raise_on_send=httpx.ConnectError("Connection refused"))
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 502
        assert response.headers.get("x-ongarde-block") is None
        assert "x-ongarde-block" not in {k.lower() for k in response.headers}

    def test_timeout_exception_returns_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 12: httpx.TimeoutException → HTTP 502."""
        upstream = _MockUpstream(
            raise_on_send=httpx.TimeoutException("Upstream timed out")
        )
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 502

    def test_timeout_exception_no_x_ongarde_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06: TimeoutException 502 MUST NOT have X-OnGarde-Block header."""
        upstream = _MockUpstream(
            raise_on_send=httpx.TimeoutException("Upstream timed out")
        )
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.headers.get("x-ongarde-block") is None

    def test_remote_protocol_error_returns_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 13: httpx.RemoteProtocolError → HTTP 502."""
        upstream = _MockUpstream(
            raise_on_send=httpx.RemoteProtocolError("Invalid HTTP")
        )
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 502

    def test_remote_protocol_error_no_x_ongarde_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-E001-06: RemoteProtocolError 502 MUST NOT have X-OnGarde-Block header."""
        upstream = _MockUpstream(
            raise_on_send=httpx.RemoteProtocolError("Invalid HTTP")
        )
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.headers.get("x-ongarde-block") is None

    def test_502_body_has_correct_schema(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06 item 1: 502 body schema is correct."""
        upstream = _MockUpstream(raise_on_send=httpx.ConnectError("Connection refused"))
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        body = response.json()
        assert "error" in body
        assert body["error"]["message"] == "Upstream LLM provider unavailable"
        assert body["error"]["code"] == "upstream_unavailable"

    def test_502_has_scan_id_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """502 response includes X-OnGarde-Scan-ID for audit correlation."""
        upstream = _MockUpstream(raise_on_send=httpx.ConnectError("Connection refused"))
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 502
        # X-OnGarde-Scan-ID should be present (set by build_upstream_unavailable_response)
        scan_id_header = response.headers.get("x-ongarde-scan-id")
        assert scan_id_header is not None
        assert len(scan_id_header) == 26  # valid ULID length


# ─── Upstream HTTP errors → passthrough (NOT 502) ─────────────────────────────


class TestUpstreamHttpErrorPassthrough:
    """AC-E001-06 item 4: upstream HTTP 4xx/5xx are passed through, NOT converted to 502."""

    @pytest.mark.parametrize("status_code", [429, 401, 403, 500, 503])
    def test_upstream_http_errors_passed_through(
        self, monkeypatch: pytest.MonkeyPatch, status_code: int
    ) -> None:
        """Upstream HTTP error codes are forwarded to the agent unchanged."""
        body = json.dumps({"error": {"message": "Quota exceeded", "code": "rate_limit_exceeded"}}).encode()
        upstream = _MockUpstream(status_code=status_code, body=body)
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        # Status code must be the upstream's code, NOT 502
        assert response.status_code == status_code
        assert response.status_code != 502

    def test_upstream_429_not_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06: 429 Too Many Requests from upstream → 429 to agent (not 502)."""
        upstream = _MockUpstream(status_code=429, body=b'{"error":{"message":"rate limited"}}')
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 429

    def test_upstream_401_not_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E001-06: 401 Unauthorized from upstream → 401 to agent (not 502)."""
        upstream = _MockUpstream(status_code=401, body=b'{"error":{"message":"invalid key"}}')
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 401

    def test_upstream_error_no_x_ongarde_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Upstream HTTP errors do NOT set X-OnGarde-Block (not a policy block)."""
        upstream = _MockUpstream(status_code=429, body=b'{"error":{"message":"rate limited"}}')
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.headers.get("x-ongarde-block") is None


# ─── Invalid URL → 500 ────────────────────────────────────────────────────────


class TestInvalidUrlHandling:
    """AC-E001-06 item 15: httpx.InvalidURL → HTTP 500 (configuration error)."""

    def test_invalid_url_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """httpx.InvalidURL → HTTP 500 (config error, not a scan block or gateway error)."""
        upstream = _MockUpstream(raise_on_send=httpx.InvalidURL("Bad URL"))
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 500

    def test_invalid_url_no_x_ongarde_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """InvalidURL 500 is NOT a policy block — no X-OnGarde-Block header."""
        upstream = _MockUpstream(raise_on_send=httpx.InvalidURL("Bad URL"))
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.headers.get("x-ongarde-block") is None


# ─── Failure mode confusion prevention ───────────────────────────────────────


class TestFailureModeDistinctionIntegration:
    """AC-E001-06 item 3: verify that 502 and 400 BLOCK are never confused."""

    def test_connect_error_not_block_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConnectError → 502, never 400 BLOCK."""
        upstream = _MockUpstream(raise_on_send=httpx.ConnectError("Connection refused"))
        with TestClient(_build_test_app(upstream, monkeypatch)) as client:
            response = _make_request(client)
        assert response.status_code == 502
        assert response.status_code != 400
        assert response.headers.get("x-ongarde-block") is None

    def test_block_response_not_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Action.BLOCK → 400, never 502."""
        upstream = _MockUpstream(status_code=200)
        block_result = ScanResult(action=Action.BLOCK, scan_id="01BLOCKED000000000000000000")
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                response = _make_request(client)
        assert response.status_code == 400
        assert response.status_code != 502
        assert response.headers.get("x-ongarde-block") == "true"

    def test_block_sets_x_ongarde_block_502_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The critical separation: X-OnGarde-Block present only on policy BLOCK."""
        # Test BLOCK
        upstream_allow = _MockUpstream(status_code=200)
        block_result = ScanResult(action=Action.BLOCK, scan_id="01BLOCKED000000000000000000")
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream_allow, monkeypatch)) as client:
                block_resp = _make_request(client)

        # Test 502
        upstream_down = _MockUpstream(raise_on_send=httpx.ConnectError("Down"))
        with TestClient(_build_test_app(upstream_down, monkeypatch)) as client:
            unavail_resp = _make_request(client)

        assert block_resp.headers.get("x-ongarde-block") == "true"
        assert unavail_resp.headers.get("x-ongarde-block") is None

    def test_error_codes_distinguish_modes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Error body codes distinguish security BLOCK from connectivity failure."""
        # BLOCK body
        upstream = _MockUpstream(status_code=200)
        block_result = ScanResult(action=Action.BLOCK, scan_id="01BLOCKED000000000000000000")
        with patch(
            "app.proxy.engine.scan_or_block",
            new_callable=AsyncMock,
            return_value=block_result,
        ):
            with TestClient(_build_test_app(upstream, monkeypatch)) as client:
                block_resp = _make_request(client)

        # 502 body
        upstream_down = _MockUpstream(raise_on_send=httpx.ConnectError("Down"))
        with TestClient(_build_test_app(upstream_down, monkeypatch)) as client:
            unavail_resp = _make_request(client)

        block_body = block_resp.json()
        unavail_body = unavail_resp.json()

        assert block_body["error"]["code"] == "policy_violation"
        assert unavail_body["error"]["code"] == "upstream_unavailable"
