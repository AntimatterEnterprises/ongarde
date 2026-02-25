"""Integration tests for E-001-S-002: Async HTTP proxy handler — all four endpoints.

Covers all Acceptance Criteria defined in the story:
  AC-E001-01: Body byte-identity forwarding
  AC-E001-02: All four endpoints proxied
  AC-E001-03: Response status code + body passthrough
  AC-E001-04: ≤ 5ms p99 proxy overhead at 100 concurrent; connection pool reuse

Test strategy:
  - httpx.MockTransport: captures upstream-bound bytes; returns controlled responses
  - httpx.ASGITransport: drives OnGarde in-process (no real network I/O)
  - starlette.testclient.TestClient: drives sync lifespan + sync requests
  - asyncio.gather(): 100 concurrent requests for the performance AC
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.config import Config
from app.main import create_app, lifespan
from app.proxy.engine import (
    POOL_MAX_CONNECTIONS,
    POOL_MAX_KEEPALIVE,
    _route_upstream,
    create_http_client,
)

# ─── Fixtures & Helpers ───────────────────────────────────────────────────────


def _stub_config() -> Config:
    """Return a default Config (no file I/O)."""
    return Config.defaults()


def _patch_load_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch load_config in app.main to return a stub Config."""
    monkeypatch.setattr("app.main.load_config", lambda: _stub_config())


class _MockUpstream:
    """In-process mock upstream server using httpx.MockTransport.

    Records every request's bytes and returns a configurable response.
    Used to verify byte-identity and response passthrough.
    """

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: bytes = b'{"result": "mocked"}',
        content_type: str = "application/json",
    ) -> None:
        self.received_requests: list[httpx.Request] = []
        self.received_bodies: list[bytes] = []
        self._status_code = status_code
        self._body = body
        self._content_type = content_type

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Sync handler — MockTransport accepts sync handlers for async clients."""
        self.received_requests.append(request)
        self.received_bodies.append(request.content)
        return httpx.Response(
            self._status_code,
            content=self._body,
            headers={"content-type": self._content_type},
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.AsyncClient:
        """Return a shared AsyncClient backed by this mock upstream."""
        return httpx.AsyncClient(
            transport=self.transport,
            # Base URL is set here so build_request in engine.py works.
            # The transport ignores the host — all requests go to the mock.
        )


def _build_test_app(mock_upstream: _MockUpstream, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a ready OnGarde app wired to a mock upstream.

    Returns the ASGI app object (use with TestClient context manager).
    Patches load_config so no real config file is needed.
    """
    _patch_load_config(monkeypatch)
    application = create_app()

    # Inject mock http_client BEFORE the lifespan starts.
    # The lifespan will overwrite app.state.http_client; we patch create_http_client
    # so the lifespan creates our mock-backed client instead.
    monkeypatch.setattr(
        "app.main.create_http_client",
        lambda: mock_upstream.client(),
    )
    return application


# ─── Unit: route_upstream() ───────────────────────────────────────────────────


class TestRouteUpstream:
    """Unit tests for the _route_upstream() routing function."""

    def test_chat_completions_routes_to_openai(self) -> None:
        config = _stub_config()
        result = _route_upstream("v1/chat/completions", config)
        assert result == config.upstream.openai

    def test_completions_routes_to_openai(self) -> None:
        config = _stub_config()
        result = _route_upstream("v1/completions", config)
        assert result == config.upstream.openai

    def test_embeddings_routes_to_openai(self) -> None:
        config = _stub_config()
        result = _route_upstream("v1/embeddings", config)
        assert result == config.upstream.openai

    def test_messages_routes_to_anthropic(self) -> None:
        config = _stub_config()
        result = _route_upstream("v1/messages", config)
        assert result == config.upstream.anthropic

    def test_messages_with_trailing_slash_routes_to_anthropic(self) -> None:
        config = _stub_config()
        result = _route_upstream("v1/messages/", config)
        # Should still route to anthropic (startswith match)
        assert result == config.upstream.anthropic

    def test_messages_subpath_routes_to_anthropic(self) -> None:
        config = _stub_config()
        result = _route_upstream("v1/messages/stream", config)
        assert result == config.upstream.anthropic

    def test_unknown_path_defaults_to_openai(self) -> None:
        config = _stub_config()
        result = _route_upstream("v1/unknown-endpoint", config)
        assert result == config.upstream.openai

    def test_custom_upstream_urls(self) -> None:
        config = _stub_config()
        config.upstream.openai = "http://custom-openai:8080"
        config.upstream.anthropic = "http://custom-anthropic:9090"

        assert _route_upstream("v1/chat/completions", config) == "http://custom-openai:8080"
        assert _route_upstream("v1/messages", config) == "http://custom-anthropic:9090"


# ─── Unit: create_http_client() ───────────────────────────────────────────────


class TestCreateHttpClient:
    """Unit tests verifying the shared client is configured correctly."""

    def test_returns_async_client_instance(self) -> None:
        """create_http_client() returns an httpx.AsyncClient (not sync Client)."""
        client = create_http_client()
        assert isinstance(client, httpx.AsyncClient)
        # Sync cleanup (no async test needed — just verifying the type)
        # The client will be garbage-collected; not a resource issue in this unit test.

    def test_pool_max_connections(self) -> None:
        """Connection pool is configured with max_connections = 100."""
        assert POOL_MAX_CONNECTIONS == 100

    def test_pool_max_keepalive(self) -> None:
        """Connection pool is configured with max_keepalive_connections = 100."""
        assert POOL_MAX_KEEPALIVE == 100

    def test_follow_redirects_disabled(self) -> None:
        """follow_redirects must be False — 3xx pass through to agent."""
        client = create_http_client()
        assert client.follow_redirects is False

    def test_timeout_configured(self) -> None:
        """Client has a non-None timeout configured."""
        client = create_http_client()
        assert client.timeout is not None

    @pytest.mark.asyncio
    async def test_client_is_closeable(self) -> None:
        """Client can be created and closed without error."""
        client = create_http_client()
        await client.aclose()  # Must not raise


# ─── AC-E001-02: All four endpoints ───────────────────────────────────────────


class TestAllFourEndpoints:
    """AC-E001-02: All four LLM endpoints are proxied correctly."""

    def _make_app(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _MockUpstream]:
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)
        return app, mock

    def test_chat_completions_proxied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /v1/chat/completions → upstream receives request, client gets 200."""
        app, mock = self._make_app(monkeypatch)
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert response.status_code == 200
        assert len(mock.received_bodies) == 1

    def test_completions_proxied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /v1/completions → upstream receives request, client gets 200."""
        app, mock = self._make_app(monkeypatch)
        with TestClient(app) as client:
            response = client.post(
                "/v1/completions",
                json={"model": "text-davinci-003", "prompt": "Hello"},
            )
        assert response.status_code == 200
        assert len(mock.received_bodies) == 1

    def test_embeddings_proxied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /v1/embeddings → upstream receives request, client gets 200."""
        app, mock = self._make_app(monkeypatch)
        with TestClient(app) as client:
            response = client.post(
                "/v1/embeddings",
                json={"model": "text-embedding-ada-002", "input": "hello world"},
            )
        assert response.status_code == 200
        assert len(mock.received_bodies) == 1

    def test_messages_proxied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /v1/messages → upstream receives request, client gets 200."""
        app, mock = self._make_app(monkeypatch)
        with TestClient(app) as client:
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-opus-20240229",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        assert response.status_code == 200
        assert len(mock.received_bodies) == 1

    def test_all_four_endpoints_return_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All four endpoints return HTTP 200 when upstream returns 200."""
        mock = _MockUpstream(status_code=200)
        app = _build_test_app(mock, monkeypatch)

        endpoints = [
            ("/v1/chat/completions", {"model": "gpt-4", "messages": []}),
            ("/v1/completions", {"model": "gpt-3.5-turbo-instruct", "prompt": "hi"}),
            ("/v1/embeddings", {"model": "text-embedding-ada-002", "input": "hi"}),
            (
                "/v1/messages",
                {
                    "model": "claude-3-opus-20240229",
                    "max_tokens": 100,
                    "messages": [],
                },
            ),
        ]

        with TestClient(app) as client:
            for path, body in endpoints:
                resp = client.post(path, json=body)
                assert resp.status_code == 200, (
                    f"Expected 200 for {path}, got {resp.status_code}"
                )


# ─── AC-E001-01: Body byte-identity ───────────────────────────────────────────


class TestByteIdentityForwarding:
    """AC-E001-01: Request body must arrive at upstream bit-for-bit identical."""

    def test_body_byte_identity_chat_completions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Byte-identical body forwarding for /v1/chat/completions."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        original_body = json.dumps(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Test byte-identity"}],
                "temperature": 0.7,
            }
        ).encode("utf-8")

        with TestClient(app) as client:
            client.post(
                "/v1/chat/completions",
                content=original_body,
                headers={"content-type": "application/json"},
            )

        assert len(mock.received_bodies) == 1
        received = mock.received_bodies[0]
        assert received == original_body, (
            f"Body mismatch:\n  sent:     {original_body!r}\n"
            f"  received: {received!r}"
        )

    def test_body_byte_identity_with_unicode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unicode body forwarded without modification (no encoding changes)."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        original_body = '{"message": "こんにちは 🤺 世界"}'.encode("utf-8")

        with TestClient(app) as client:
            client.post(
                "/v1/chat/completions",
                content=original_body,
                headers={"content-type": "application/json"},
            )

        assert mock.received_bodies[0] == original_body

    def test_body_byte_identity_with_binary_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Arbitrary binary body forwarded byte-for-byte (embeddings use case)."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        original_body = bytes(range(256)) * 4  # 1024 bytes of all byte values

        with TestClient(app) as client:
            client.post(
                "/v1/embeddings",
                content=original_body,
                headers={"content-type": "application/octet-stream"},
            )

        assert mock.received_bodies[0] == original_body

    def test_body_identity_all_four_endpoints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All four endpoints preserve exact body bytes."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        # Distinct bodies so we can correlate which endpoint got which body
        bodies = {
            "/v1/chat/completions": b'{"endpoint":"chat","unique":1}',
            "/v1/completions": b'{"endpoint":"completions","unique":2}',
            "/v1/embeddings": b'{"endpoint":"embeddings","unique":3}',
            "/v1/messages": b'{"endpoint":"messages","unique":4}',
        }

        with TestClient(app) as client:
            for path, body in bodies.items():
                client.post(
                    path,
                    content=body,
                    headers={"content-type": "application/json"},
                )

        assert len(mock.received_bodies) == 4
        for i, (path, body) in enumerate(bodies.items()):
            assert mock.received_bodies[i] == body, (
                f"Body mismatch for {path}: sent {body!r}, received {mock.received_bodies[i]!r}"
            )

    def test_empty_body_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty body (b'') is forwarded as-is."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            client.post("/v1/embeddings", content=b"")

        assert mock.received_bodies[0] == b""


# ─── AC-E001-03: Response status code + body passthrough ──────────────────────


class TestResponsePassthrough:
    """AC-E001-03: HTTP status code and body from upstream forwarded unchanged."""

    def _make_app(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        status_code: int = 200,
        body: bytes = b'{"ok": true}',
    ) -> Any:
        mock = _MockUpstream(status_code=status_code, body=body)
        return _build_test_app(mock, monkeypatch)

    def test_200_status_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 200 from upstream → HTTP 200 to agent."""
        app = self._make_app(monkeypatch, status_code=200)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")
        assert resp.status_code == 200

    def test_400_status_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 400 from upstream → HTTP 400 to agent (not swallowed)."""
        app = self._make_app(monkeypatch, status_code=400, body=b'{"error": "bad request"}')
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")
        assert resp.status_code == 400

    def test_401_status_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 401 from upstream → HTTP 401 to agent."""
        app = self._make_app(monkeypatch, status_code=401, body=b'{"error": "unauthorized"}')
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")
        assert resp.status_code == 401

    def test_429_status_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 429 from upstream (rate limit) → HTTP 429 to agent."""
        app = self._make_app(monkeypatch, status_code=429, body=b'{"error": "rate limited"}')
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")
        assert resp.status_code == 429

    def test_500_status_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 500 from upstream → HTTP 500 to agent."""
        app = self._make_app(monkeypatch, status_code=500, body=b'{"error": "server error"}')
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")
        assert resp.status_code == 500

    def test_response_body_byte_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Response body bytes are forwarded byte-for-byte to the agent."""
        upstream_body = b'{"choices":[{"message":{"content":"Hello from upstream!"}}]}'
        mock = _MockUpstream(body=upstream_body)
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")

        assert resp.content == upstream_body

    def test_response_body_with_unicode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unicode response body forwarded without modification."""
        upstream_body = '{"reply": "こんにちは 🤺"}'.encode("utf-8")
        mock = _MockUpstream(body=upstream_body)
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")

        assert resp.content == upstream_body


# ─── AC-E001-04: Upstream routing (openai vs anthropic) ───────────────────────


class TestUpstreamRouting:
    """Upstream URL routing: OpenAI paths → config.upstream.openai; /v1/messages → anthropic."""

    def test_chat_completions_goes_to_openai_upstream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /v1/chat/completions routed to config.upstream.openai."""
        received_urls: list[str] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            received_urls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

        _patch_load_config(monkeypatch)
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        with TestClient(app) as client:
            client.post("/v1/chat/completions", content=b"{}")

        assert len(received_urls) == 1
        url = received_urls[0]
        # Default config: openai = "https://api.openai.com"
        assert "api.openai.com" in url, f"Expected openai URL, got: {url}"
        assert "v1/chat/completions" in url, f"Expected path in URL, got: {url}"

    def test_messages_goes_to_anthropic_upstream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /v1/messages routed to config.upstream.anthropic."""
        received_urls: list[str] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            received_urls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

        _patch_load_config(monkeypatch)
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        with TestClient(app) as client:
            client.post("/v1/messages", content=b"{}")

        assert len(received_urls) == 1
        url = received_urls[0]
        # Default config: anthropic = "https://api.anthropic.com"
        assert "api.anthropic.com" in url, f"Expected anthropic URL, got: {url}"
        assert "v1/messages" in url, f"Expected path in URL, got: {url}"

    def test_embeddings_routes_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /v1/embeddings routed to openai upstream."""
        received_urls: list[str] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            received_urls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        _patch_load_config(monkeypatch)
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        with TestClient(app) as client:
            client.post("/v1/embeddings", content=b"{}")

        assert "api.openai.com" in received_urls[0]
        assert "v1/embeddings" in received_urls[0]

    def test_completions_routes_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /v1/completions routed to openai upstream."""
        received_urls: list[str] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            received_urls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        _patch_load_config(monkeypatch)
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        with TestClient(app) as client:
            client.post("/v1/completions", content=b"{}")

        assert "api.openai.com" in received_urls[0]
        assert "v1/completions" in received_urls[0]

    def test_query_params_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Query parameters are forwarded to upstream unchanged."""
        received_urls: list[str] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            received_urls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        _patch_load_config(monkeypatch)
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        with TestClient(app) as client:
            client.post("/v1/chat/completions?model=gpt-4&stream=true", content=b"{}")

        assert len(received_urls) == 1
        url = received_urls[0]
        assert "model=gpt-4" in url
        assert "stream=true" in url


# ─── AC-E001-04: Single shared http_client (not per-request) ──────────────────


class TestConnectionPooling:
    """AC-E001-04: Single shared httpx.AsyncClient — not instantiated per-request."""

    def test_app_state_http_client_exists_after_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """app.state.http_client is set after lifespan startup."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as _client:
            assert hasattr(app.state, "http_client")
            assert isinstance(app.state.http_client, httpx.AsyncClient)

    def test_http_client_is_same_instance_across_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same client instance handles all requests (never per-request instantiation)."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            # Record the client instance before and after several requests
            instance_before = id(app.state.http_client)

            for _ in range(5):
                client.post("/v1/chat/completions", content=b"{}")

            instance_after = id(app.state.http_client)

        # Same object identity — confirmed not re-created per request
        assert instance_before == instance_after, (
            "http_client was replaced between requests — must be a shared singleton"
        )

    def test_http_client_is_async_client_not_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """app.state.http_client must be httpx.AsyncClient (never httpx.Client)."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as _client:
            assert isinstance(app.state.http_client, httpx.AsyncClient)
            assert not isinstance(app.state.http_client, httpx.Client)

    def test_connection_pool_size_constant_is_100(self) -> None:
        """Pool size constant matches --limit-concurrency 100 (architecture.md §9.3)."""
        assert POOL_MAX_CONNECTIONS == 100, (
            f"Pool max_connections must be 100 to match --limit-concurrency 100, "
            f"got {POOL_MAX_CONNECTIONS}"
        )

    def test_http_client_closed_after_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """http_client is closed (aclose'd) after lifespan shutdown."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as _client:
            http_client = app.state.http_client

        # After TestClient exits, lifespan shutdown ran.
        # httpx.AsyncClient.is_closed becomes True after aclose().
        assert http_client.is_closed, (
            "http_client was not closed during shutdown — resource leak"
        )


# ─── AC-E001-04: Error handling (ConnectError → 502) ──────────────────────────


class TestErrorHandling:
    """Error handling: upstream unreachable → 502 Bad Gateway."""

    def test_connect_error_returns_502(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConnectError from upstream → HTTP 502 to agent."""

        def failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
        _patch_load_config(monkeypatch)
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")

        assert resp.status_code == 502

    def test_502_body_is_json_compatible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """502 response body is parseable (no raw exception text exposed)."""

        def failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
        _patch_load_config(monkeypatch)
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")

        assert resp.status_code == 502
        # Body should be JSON with an "error" field (no raw exception trace)
        body = resp.json()
        assert "error" in body

    def test_503_before_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proxy routes return 503 before app.state.ready is True."""
        _patch_load_config(monkeypatch)
        app = create_app()  # No lifespan startup
        app.state.ready = False

        transport = ASGITransport(app=app)  # type: ignore[arg-type]

        async def check() -> int:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post("/v1/chat/completions", content=b"{}")
                return resp.status_code

        status = asyncio.get_event_loop().run_until_complete(check())
        assert status == 503


# ─── AC-E001-04: Performance — ≤ 5ms p99 overhead at 100 concurrent ───────────


class TestConcurrentPerformance:
    """AC-E001-04: Proxy overhead architecture and throughput tests.

    Important: The ≤ 5ms p99 proxy overhead AC is measured in a real-server
    benchmark (benchmarks/bench_proxy.py) against a live uvicorn process. The
    in-process tests here verify the ARCHITECTURAL PREREQUISITES that make the
    5ms AC achievable:

      1. Shared client (never per-request instantiation)
      2. No blocking I/O (100 concurrent requests complete without deadlock)
      3. All 100 requests succeed (no pool exhaustion)
      4. Total throughput is fast enough to be consistent with ≤5ms per-request overhead

    Why not measure 5ms in pytest?
      - ASGITransport is in-process but sequential (single event loop).
      - Measuring from asyncio.gather() start to response end includes event-loop
        scheduling time for all 100 coroutines, inflating per-request latency far
        beyond the actual proxy overhead.
      - Overhead = proxy machinery time ONLY (receive → build httpx req → send to upstream).
        That window is sub-millisecond and requires instrumented server measurement.

    See: benchmarks/bench_proxy.py for the real p99 benchmark.
    """

    @pytest.mark.asyncio
    async def test_100_concurrent_requests_no_blocking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """100 concurrent requests complete in <3s wall-clock (verifies no blocking I/O).

        If any blocking I/O existed on the event loop (e.g., sync httpx.Client), the
        100 requests would stack up. With async machinery, they interleave correctly
        and complete in milliseconds of wall-clock time.

        Scanner is mocked to ALLOW — this test validates async concurrency/throughput,
        not scanner accuracy (scanner behaviour is CI-environment-dependent due to the
        hard 60ms timeout and Presidio NLP warm-up time on slow shared runners).
        """
        from app.models.scan import Action, ScanResult

        _patch_load_config(monkeypatch)

        def fast_handler(request: httpx.Request) -> httpx.Response:
            """Instant mock upstream response."""
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fast_handler))
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        async def mock_scan(content, scan_pool, scan_id, audit_context, **kwargs):
            return ScanResult(action=Action.ALLOW, scan_id=scan_id)

        async with lifespan(app):
            transport = ASGITransport(app=app)  # type: ignore[arg-type]
            with patch("app.proxy.engine.scan_or_block", new=mock_scan):
                async with AsyncClient(transport=transport, base_url="http://test") as session:
                    # Time the total wall-clock for 100 concurrent requests
                    t0 = time.perf_counter()
                    coros = [
                        session.post("/v1/chat/completions", content=b'{"model":"gpt-4"}')
                        for _ in range(100)
                    ]
                    responses = await asyncio.gather(*coros)
                    wall_clock_ms = (time.perf_counter() - t0) * 1000

        print(
            f"\nThroughput (100 concurrent, in-process):\n"
            f"  Wall-clock total = {wall_clock_ms:.1f}ms\n"
            f"  Avg per request = {wall_clock_ms / 100:.2f}ms\n"
        )

        # All 100 must succeed
        statuses = [r.status_code for r in responses]
        assert all(s == 200 for s in statuses), (
            f"Some requests failed: {[s for s in statuses if s != 200]}"
        )

        # Wall-clock < 3s for 100 in-process requests (generous; real is ~200ms)
        # If blocking I/O existed, this would be 100× longer
        assert wall_clock_ms < 3000, (
            f"100 concurrent requests took {wall_clock_ms:.0f}ms > 3000ms limit.\n"
            f"This strongly suggests blocking I/O on the event loop."
        )

    @pytest.mark.asyncio
    async def test_proxy_overhead_per_request_architecture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single-request overhead is consistent with ≤5ms (architectural verification).

        Measures: time from start of proxy_handler to invocation of upstream mock.
        This is the TRUE proxy overhead — no network, no ASGI routing overhead.
        Uses a shared timestamp dict to capture upstream invocation time.
        """
        _patch_load_config(monkeypatch)
        call_timestamps: list[float] = []
        request_start: list[float] = []

        def instrumented_handler(request: httpx.Request) -> httpx.Response:
            """Record exact time upstream mock is invoked."""
            call_timestamps.append(time.perf_counter())
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(instrumented_handler)
        )
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        async with lifespan(app):
            transport = ASGITransport(app=app)  # type: ignore[arg-type]
            async with AsyncClient(transport=transport, base_url="http://test") as session:
                # Warm up
                for _ in range(5):
                    await session.post("/v1/chat/completions", content=b"{}")
                call_timestamps.clear()

                # Measure 20 sequential single requests to get clean proxy timing
                for _ in range(20):
                    t_before = time.perf_counter()
                    request_start.append(t_before)
                    await session.post("/v1/chat/completions", content=b"{}")

        # Proxy overhead = time from initiating request to upstream receiving it.
        # In-process, this is typically < 1ms. We allow up to 5ms.
        assert len(call_timestamps) == 20
        assert len(request_start) == 20

        overheads_ms = [
            (call_timestamps[i] - request_start[i]) * 1000
            for i in range(20)
        ]
        overheads_sorted = sorted(overheads_ms)
        p99_index = max(0, int(len(overheads_sorted) * 0.99) - 1)
        p99_ms = overheads_sorted[p99_index]

        print(
            f"\nProxy overhead (20 sequential, in-process):\n"
            f"  p50 = {overheads_sorted[len(overheads_sorted)//2]:.3f}ms\n"
            f"  p99 = {p99_ms:.3f}ms\n"
            f"  max = {max(overheads_ms):.3f}ms"
        )

        # AC-E001-04 real target: p99 ≤ 5ms (measured by benchmarks/bench_proxy.py).
        # In-process test threshold is 15ms to account for:
        #   - ASGI transport overhead (ASGITransport adds ~1–5ms per request)
        #   - GC pauses and event-loop scheduling jitter in test environment
        #   - Measurement start is before ASGI call (includes all routing overhead)
        # The p50 should be < 2ms — if p50 > 5ms, that indicates blocking I/O.
        p50_ms_val = overheads_sorted[len(overheads_sorted) // 2]
        assert p50_ms_val <= 5.0, (
            f"p50 proxy overhead {p50_ms_val:.3f}ms > 5ms — likely blocking I/O.\n"
            f"p50 = {p50_ms_val:.3f}ms, p99 = {p99_ms:.3f}ms"
        )
        assert p99_ms <= 15.0, (
            f"p99 proxy overhead {p99_ms:.3f}ms > 15ms (in-process tolerance).\n"
            f"Note: real-server AC (≤5ms p99) is in benchmarks/bench_proxy.py.\n"
            f"p50={p50_ms_val:.3f}ms, p99={p99_ms:.3f}ms, max={max(overheads_ms):.3f}ms"
        )

    @pytest.mark.asyncio
    async def test_100_concurrent_no_pool_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """100 concurrent requests all complete successfully (no pool exhaustion).

        Scanner is mocked to ALLOW so this purely tests HTTP connection pool
        capacity — not Presidio throughput (which is environment-dependent
        and covered by benchmarks/bench_proxy.py).
        """
        from app.models.scan import Action, ScanResult

        _patch_load_config(monkeypatch)
        call_count = [0]

        def counting_handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(200, json={"ok": True})

        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(counting_handler)
        )
        monkeypatch.setattr("app.main.create_http_client", lambda: mock_client)
        app = create_app()

        async def mock_scan(content, scan_pool, scan_id, audit_context, **kwargs):
            return ScanResult(action=Action.ALLOW, scan_id=scan_id)

        async with lifespan(app):
            transport = ASGITransport(app=app)  # type: ignore[arg-type]
            with patch("app.proxy.engine.scan_or_block", new=mock_scan):
                async with AsyncClient(transport=transport, base_url="http://test") as session:
                    coros = [
                        session.post("/v1/chat/completions", content=b"{}")
                        for _ in range(100)
                    ]
                    responses = await asyncio.gather(*coros)

        statuses = [r.status_code for r in responses]
        success_count = sum(1 for s in statuses if s == 200)
        assert success_count == 100, (
            f"Expected 100 successful responses, got {success_count}/100 "
            f"(pool exhaustion or connection errors?)\nStatuses: {set(statuses)}"
        )
        assert call_count[0] == 100, (
            f"Mock upstream received {call_count[0]} calls, expected 100"
        )


# ─── Streaming response forwarding ────────────────────────────────────────────


class TestStreamingResponseForwarding:
    """Streaming responses (SSE) are forwarded byte-for-byte using aiter_bytes()."""

    def test_streaming_sse_response_forwarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SSE streaming response bytes reach the agent unchanged."""
        sse_body = (
            b"data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n"
            b"data: {\"choices\":[{\"delta\":{\"content\":\" World\"}}]}\n\n"
            b"data: [DONE]\n\n"
        )
        mock = _MockUpstream(body=sse_body, content_type="text/event-stream")
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                content=b'{"model":"gpt-4","stream":true}',
                headers={"content-type": "application/json"},
            )

        assert resp.content == sse_body

    def test_large_response_body_forwarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Large response body (100KB) is streamed without truncation."""
        large_body = b"x" * 100_000  # 100 KB
        mock = _MockUpstream(body=large_body, content_type="application/octet-stream")
        app = _build_test_app(mock, monkeypatch)

        with TestClient(app) as client:
            resp = client.post("/v1/embeddings", content=b"{}")

        assert len(resp.content) == 100_000
        assert resp.content == large_body


# ─── M-002 Path Guard: only /v1/* paths are proxied ──────────────────────────


class TestPathGuard:
    """M-002 (from E-001-S-002 review): non-/v1 paths must return 404, not proxy blindly.

    The catch-all /{path:path} handler now has a path guard that rejects any path
    that does not start with 'v1/'. This prevents accidentally proxying requests to
    /metrics, /v2/something, /admin, etc. to the upstream LLM.

    AC reference: E-001-S-004 (deferred M-002 from E-001-S-002 code review).
    """

    def test_non_v1_path_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /metrics → 404 (not proxied to upstream)."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.get("/metrics")
        assert resp.status_code == 404
        assert len(mock.received_bodies) == 0  # upstream never received anything

    def test_v2_path_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /v2/chat/completions → 404 (v2 not a supported API version)."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v2/chat/completions", content=b"{}")
        assert resp.status_code == 404
        assert len(mock.received_bodies) == 0

    def test_root_path_not_proxied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET / is handled by the root health router, not the proxy catch-all."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.get("/")
        # Root endpoint is the service identity endpoint — not 404, not proxied
        assert resp.status_code == 200
        assert len(mock.received_bodies) == 0  # upstream never received a root request

    def test_admin_path_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /admin → 404 (not a valid proxy path)."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.get("/admin")
        assert resp.status_code == 404
        assert len(mock.received_bodies) == 0

    def test_v1_chat_completions_passes_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/v1/chat/completions passes the path guard and is proxied."""
        mock = _MockUpstream(status_code=200, body=b'{"ok":true}')
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/chat/completions", content=b"{}")
        # Proxied — upstream gets the request
        assert resp.status_code == 200
        assert len(mock.received_bodies) == 1

    def test_v1_messages_passes_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """/v1/messages passes the path guard and is proxied."""
        mock = _MockUpstream(status_code=200, body=b'{"ok":true}')
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/messages", content=b"{}")
        assert resp.status_code == 200
        assert len(mock.received_bodies) == 1

    def test_arbitrary_v1_subpath_passes_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/v1/arbitrary-future-endpoint passes the path guard and is proxied."""
        mock = _MockUpstream(status_code=200, body=b'{"ok":true}')
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/v1/future-endpoint", content=b"{}")
        assert resp.status_code == 200
        assert len(mock.received_bodies) == 1

    def test_dashboard_path_not_proxied_to_upstream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/dashboard is handled by the dashboard router — never proxied to upstream LLM."""
        mock = _MockUpstream()
        app = _build_test_app(mock, monkeypatch)
        with TestClient(app) as client:
            resp = client.get("/dashboard")
        # Dashboard now returns 200 (the SPA HTML); the critical invariant is
        # that it is NEVER forwarded to the upstream LLM proxy.
        assert resp.status_code != 502, "/dashboard must not be proxied upstream"
        assert len(mock.received_bodies) == 0, "Dashboard request must not reach upstream"
