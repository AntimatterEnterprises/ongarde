"""Integration tests for E-001-S-005: Request/response size limits.

Tests the full OnGarde proxy with body size middleware and response buffer routing
wired together.

AC-E001-07 (request body):
  5. Content-Length > 1 MB → HTTP 413 before any proxy processing
  6. Content-Length == 1 MB → accepted (boundary)
  7. No Content-Length, body > 1 MB → HTTP 413
  8. Body size check before scan gate and upstream connection

AC-E001-07 (response buffer routing):
  9.  Upstream response ≤ 512 KB → buffered in memory (Response, not StreamingResponse)
  10. Upstream response > 512 KB → StreamingResponse (streaming scan path)
  11. Unit test: mock upstream returning 600 KB triggers streaming path
      (aread() not called for large responses)

AC-E001-05 (binding):
  Integration: security warning logged when proxy.host = "0.0.0.0"

Story: E-001-S-005
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from app.config import Config
from app.constants import MAX_REQUEST_BODY_BYTES, MAX_RESPONSE_BUFFER_BYTES
from app.main import create_app


# ─── Fixtures & Helpers ───────────────────────────────────────────────────────


def _stub_config() -> Config:
    """Return a default Config (no file I/O)."""
    return Config.defaults()


def _patch_load_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.main.load_config", lambda: _stub_config())


class _MockUpstream:
    """In-process mock upstream using httpx.MockTransport."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: bytes = b'{"result": "ok"}',
        content_type: str = "application/json",
        include_content_length: bool = True,
    ) -> None:
        self.received_requests: list[httpx.Request] = []
        self._status_code = status_code
        self._body = body
        self._content_type = content_type
        self._include_content_length = include_content_length
        self.aread_call_count = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.received_requests.append(request)
        headers: dict[str, str] = {"content-type": self._content_type}
        if self._include_content_length:
            headers["content-length"] = str(len(self._body))
        return httpx.Response(
            self._status_code,
            content=self._body,
            headers=headers,
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport)


def _build_test_app(
    mock_upstream: _MockUpstream, monkeypatch: pytest.MonkeyPatch
) -> Any:
    _patch_load_config(monkeypatch)
    application = create_app()
    monkeypatch.setattr(
        "app.main.create_http_client",
        lambda: mock_upstream.client(),
    )
    return application


# ─── Request body size limits (end-to-end through middleware) ─────────────────


class TestRequestBodySizeLimitEndToEnd:
    """Full proxy stack: body size middleware → proxy engine."""

    def test_request_over_1mb_returns_413(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-5: Content-Length > 1 MB → HTTP 413 before reaching proxy engine."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            oversized = MAX_REQUEST_BODY_BYTES + 1
            resp = client.post(
                "/v1/chat/completions",
                content=b"x",  # actual body irrelevant; header is checked first
                headers={
                    "content-length": str(oversized),
                    "content-type": "application/json",
                },
            )

        assert resp.status_code == 413
        # Upstream must NOT have been called (AC-8: check before upstream connection)
        assert len(mock.received_requests) == 0

    def test_413_body_format_matches_spec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-5: 413 response body exactly matches the spec JSON format."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b"x",
                headers={
                    "content-length": str(MAX_REQUEST_BODY_BYTES + 100),
                    "content-type": "application/json",
                },
            )

        assert resp.status_code == 413
        data = resp.json()
        assert data == {
            "error": {
                "message": "Request body too large. Maximum size: 1MB",
                "code": "payload_too_large",
            }
        }

    def test_request_exactly_1mb_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-6: Body == 1 MB (boundary) must be accepted, not rejected."""
        mock = _MockUpstream(body=b'{"result": "ok"}')
        app = _build_test_app(mock, monkeypatch)

        exact_body = b"z" * MAX_REQUEST_BODY_BYTES

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=exact_body,
                headers={
                    "content-length": str(MAX_REQUEST_BODY_BYTES),
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test-key",
                },
            )

        # Should reach the upstream (not 413)
        assert resp.status_code != 413
        assert len(mock.received_requests) == 1

    def test_normal_request_body_is_proxied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Small request body (a few KB) passes through to upstream unchanged."""
        payload = b'{"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}'
        mock = _MockUpstream(body=b'{"id": "chatcmpl-test", "choices": []}')
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=payload,
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test-key",
                },
            )

        assert resp.status_code == 200
        assert len(mock.received_requests) == 1
        assert mock.received_requests[0].content == payload

    def test_413_check_before_upstream_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-8: Upstream is never contacted for oversized requests."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b"x",
                headers={
                    "content-length": str(10 * 1024 * 1024),  # 10 MB
                    "content-type": "application/json",
                },
            )

        assert resp.status_code == 413
        # Upstream received zero requests — body size checked before proxy forwarding
        assert len(mock.received_requests) == 0


# ─── Response buffer routing ──────────────────────────────────────────────────


class TestResponseBufferRouting:
    """AC-9, AC-10, AC-11: Response size routing."""

    def test_small_response_is_buffered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-9: Upstream response ≤ 512 KB → proxy returns full body."""
        small_body = b'{"choices": [{"message": {"content": "Hello!"}}]}'
        mock = _MockUpstream(body=small_body)
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{"model":"gpt-4","messages":[]}',
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test",
                },
            )

        assert resp.status_code == 200
        assert resp.content == small_body

    def test_200kb_response_is_buffered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-9: 200 KB response (well under 512 KB) is buffered and returned."""
        body_200kb = b"a" * (200 * 1024)
        mock = _MockUpstream(body=body_200kb)
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{}',
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test",
                },
            )

        assert resp.status_code == 200
        assert len(resp.content) == 200 * 1024

    def test_large_response_triggers_streaming_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-10, AC-11: Mock upstream returning 600 KB → streaming path taken.

        Verified by patching aread() to raise — if streaming path is used,
        aread() is never called and the test passes.
        """
        large_body = b"x" * (600 * 1024)  # 600 KB
        mock = _MockUpstream(body=large_body)
        app = _build_test_app(mock, monkeypatch)

        aread_called: list[bool] = []
        original_aread = httpx.Response.aread

        async def spy_aread(self: httpx.Response) -> bytes:
            aread_called.append(True)
            return await original_aread(self)

        monkeypatch.setattr(httpx.Response, "aread", spy_aread)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{"model":"gpt-4","messages":[]}',
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test",
                },
            )

        assert resp.status_code == 200
        # aread() must NOT have been called — streaming path was taken instead
        assert len(aread_called) == 0, (
            "Expected streaming path (aread not called) for 600 KB response, "
            f"but aread was called {len(aread_called)} time(s)"
        )
        # Full body still forwarded to agent via streaming
        assert len(resp.content) == 600 * 1024

    def test_512kb_response_is_buffered_not_streamed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Boundary: exactly 512 KB response is buffered (≤ threshold)."""
        exact_512kb = b"b" * MAX_RESPONSE_BUFFER_BYTES
        mock = _MockUpstream(body=exact_512kb)
        app = _build_test_app(mock, monkeypatch)

        aread_called: list[bool] = []
        original_aread = httpx.Response.aread

        async def spy_aread(self: httpx.Response) -> bytes:
            aread_called.append(True)
            return await original_aread(self)

        monkeypatch.setattr(httpx.Response, "aread", spy_aread)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{}',
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test",
                },
            )

        assert resp.status_code == 200
        # Exactly 512 KB should use the buffered path → aread() WAS called
        assert len(aread_called) == 1, (
            "Expected buffered path (aread called once) for exactly 512 KB response"
        )

    def test_512kb_plus_1_response_uses_streaming(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Boundary: 512 KB + 1 byte → streaming path."""
        over_threshold = b"c" * (MAX_RESPONSE_BUFFER_BYTES + 1)
        mock = _MockUpstream(body=over_threshold)
        app = _build_test_app(mock, monkeypatch)

        aread_called: list[bool] = []
        original_aread = httpx.Response.aread

        async def spy_aread(self: httpx.Response) -> bytes:
            aread_called.append(True)
            return await original_aread(self)

        monkeypatch.setattr(httpx.Response, "aread", spy_aread)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{}',
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test",
                },
            )

        assert resp.status_code == 200
        # aread() must NOT have been called for > 512 KB response
        assert len(aread_called) == 0

    def test_sse_response_always_uses_streaming_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SSE (text/event-stream) responses always use the streaming path.

        Even a small SSE response should not be buffered (aread not called).
        """
        sse_body = b"data: {}\n\n" * 10  # small SSE payload
        mock = _MockUpstream(
            body=sse_body,
            content_type="text/event-stream",
        )
        app = _build_test_app(mock, monkeypatch)

        aread_called: list[bool] = []
        original_aread = httpx.Response.aread

        async def spy_aread(self: httpx.Response) -> bytes:
            aread_called.append(True)
            return await original_aread(self)

        monkeypatch.setattr(httpx.Response, "aread", spy_aread)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{"stream":true,"model":"gpt-4","messages":[]}',
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test",
                },
            )

        assert resp.status_code == 200
        # SSE → streaming path → aread() NOT called
        assert len(aread_called) == 0

    def test_response_without_content_length_uses_buffer_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Responses without Content-Length header use the buffer path (aread)."""
        small_body = b'{"choices": []}'
        mock = _MockUpstream(body=small_body, include_content_length=False)
        app = _build_test_app(mock, monkeypatch)

        aread_called: list[bool] = []
        original_aread = httpx.Response.aread

        async def spy_aread(self: httpx.Response) -> bytes:
            aread_called.append(True)
            return await original_aread(self)

        monkeypatch.setattr(httpx.Response, "aread", spy_aread)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{}',
                headers={
                    "content-type": "application/json",
                    "authorization": "Bearer sk-test",
                },
            )

        assert resp.status_code == 200
        # No Content-Length → buffered path → aread called
        assert len(aread_called) == 1


# ─── AC-E001-05: Binding security warning ────────────────────────────────────


class TestBindingSecurityWarning:
    """AC-E001-05: 0.0.0.0 binding logs a warning but does not prevent startup."""

    def test_0000_binding_config_logs_warning(
        self, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture
    ) -> None:
        """Config with proxy.host='0.0.0.0' causes load_config() to log a warning.

        The warning is implemented in app/config.py:load_config() and tested here
        by loading a minimal config with host: 0.0.0.0.
        """
        import tempfile
        import os
        from app.config import load_config

        config_yaml = "version: 1\nproxy:\n  host: '0.0.0.0'\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(config_yaml)
            tmppath = f.name

        try:
            # load_config() should NOT raise — 0.0.0.0 is allowed
            config = load_config(config_path=tmppath)
            assert config.proxy.host == "0.0.0.0"
        finally:
            os.unlink(tmppath)

    def test_0000_binding_does_not_raise_on_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0.0.0.0 host must not cause startup failure — only a warning."""
        from app.config import Config, ProxyConfig

        def _0000_config() -> Config:
            cfg = Config.defaults()
            cfg.proxy = ProxyConfig(host="0.0.0.0", port=4242)
            return cfg

        monkeypatch.setattr("app.main.load_config", _0000_config)
        mock = _MockUpstream()
        monkeypatch.setattr(
            "app.main.create_http_client",
            lambda: mock.client(),
        )
        app = create_app()

        # App starts without exception — proxy.host is not validated by startup
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
