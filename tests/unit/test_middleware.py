"""Unit tests for app/proxy/middleware.py — body size limit middleware (E-001-S-005).

Tests the BodySizeLimitMiddleware in isolation using a minimal Starlette app.
The middleware runs before route handlers, so these tests confirm behaviour
without any proxy engine involvement.

AC-E001-07 (request body):
  5. Content-Length > 1 MB → HTTP 413 with correct JSON body
  6. Content-Length == 1 MB → accepted (boundary: exactly 1 MB passes)
  7. No Content-Length, streaming body > 1 MB → HTTP 413 (rolling cap)
  8. Body size check before scan gate and upstream connection

Story: E-001-S-005
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from app.constants import MAX_REQUEST_BODY_BYTES
from app.proxy.middleware import BodySizeLimitMiddleware

# ─── Minimal test app ─────────────────────────────────────────────────────────


async def _echo_body(request: Request) -> Response:
    """Minimal echo handler — returns the body length and first few bytes."""
    body = await request.body()
    return JSONResponse({"received_bytes": len(body), "ok": True})


def _make_test_app() -> Starlette:
    """Build a minimal Starlette app with BodySizeLimitMiddleware."""
    app = Starlette(routes=[Route("/upload", _echo_body, methods=["POST"])])
    app.add_middleware(BodySizeLimitMiddleware)
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_test_app(), raise_server_exceptions=True)


# ─── Content-Length fast path ─────────────────────────────────────────────────


class TestContentLengthFastPath:
    """Phase 1: Content-Length header present."""

    def test_content_length_exactly_1mb_is_accepted(self, client: TestClient) -> None:
        """Boundary: Content-Length == MAX_REQUEST_BODY_BYTES must pass (AC-5, AC-6)."""
        body = b"x" * MAX_REQUEST_BODY_BYTES
        response = client.post(
            "/upload",
            content=body,
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["received_bytes"] == MAX_REQUEST_BODY_BYTES

    def test_content_length_1_byte_over_limit_is_rejected(
        self, client: TestClient
    ) -> None:
        """Content-Length > MAX_REQUEST_BODY_BYTES by 1 byte → HTTP 413 (AC-5)."""
        oversized = MAX_REQUEST_BODY_BYTES + 1
        response = client.post(
            "/upload",
            content=b"x",  # actual body doesn't matter — header is checked first
            headers={
                "content-length": str(oversized),
                "content-type": "application/octet-stream",
            },
        )
        assert response.status_code == 413

    def test_content_length_2mb_is_rejected(self, client: TestClient) -> None:
        """Content-Length = 2 MB → HTTP 413."""
        response = client.post(
            "/upload",
            content=b"x",
            headers={
                "content-length": str(2 * MAX_REQUEST_BODY_BYTES),
                "content-type": "application/octet-stream",
            },
        )
        assert response.status_code == 413

    def test_413_response_body_format(self, client: TestClient) -> None:
        """HTTP 413 response body must match AC-E001-07 spec exactly (AC-5)."""
        response = client.post(
            "/upload",
            content=b"x",
            headers={
                "content-length": str(MAX_REQUEST_BODY_BYTES + 1),
                "content-type": "application/octet-stream",
            },
        )
        assert response.status_code == 413
        body = response.json()
        assert "error" in body
        assert body["error"]["message"] == "Request body too large. Maximum size: 1MB"
        assert body["error"]["code"] == "payload_too_large"

    def test_content_length_1_byte_is_accepted(self, client: TestClient) -> None:
        """Tiny request with Content-Length: 1 passes through."""
        response = client.post(
            "/upload",
            content=b"x",
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        assert response.json()["received_bytes"] == 1

    def test_invalid_content_length_returns_400(self, client: TestClient) -> None:
        """Non-integer Content-Length header → HTTP 400."""
        response = client.post(
            "/upload",
            content=b"hello",
            headers={
                "content-length": "not-a-number",
                "content-type": "application/octet-stream",
            },
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "bad_request"

    def test_content_length_zero_is_accepted(self, client: TestClient) -> None:
        """Empty body (Content-Length: 0) passes through."""
        response = client.post(
            "/upload",
            content=b"",
            headers={
                "content-length": "0",
                "content-type": "application/json",
            },
        )
        assert response.status_code == 200

    def test_content_length_large_round_number_rejected(
        self, client: TestClient
    ) -> None:
        """Content-Length: 10 MB → HTTP 413."""
        ten_mb = 10 * 1024 * 1024
        response = client.post(
            "/upload",
            content=b"x",
            headers={
                "content-length": str(ten_mb),
                "content-type": "application/octet-stream",
            },
        )
        assert response.status_code == 413


# ─── Content-Length path via bytes bodies (mislabeled legacy tests) ──────────


class TestChunkedRollingCap:
    """Phase 1 (Content-Length fast path) — despite the class name.

    NOTE: These tests pass raw ``bytes`` bodies to TestClient.  TestClient
    (backed by ``requests``) automatically injects a ``Content-Length`` header
    for fixed-length byte bodies, so ALL three tests below route through Phase 1
    of BodySizeLimitMiddleware — NOT Phase 2 (the chunked rolling cap).

    Phase 2 (genuine chunked, no Content-Length) is covered by
    ``TestChunkedPhase2`` below, which uses generator bodies so that
    ``requests`` does NOT add ``Content-Length``.

    AC-7 reference: see TestChunkedPhase2 for the real Phase 2 coverage.
    """

    def test_chunked_body_within_limit_is_accepted(self, client: TestClient) -> None:
        """Bytes body ≤ 1 MB → accepted via Phase 1 (Content-Length fast path).

        Despite the method name, TestClient injects Content-Length for bytes
        bodies, so this test exercises Phase 1 (not Phase 2 rolling cap).
        """
        small_body = b"y" * (MAX_REQUEST_BODY_BYTES // 2)  # 512 KB
        response = client.post(
            "/upload",
            content=small_body,
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["received_bytes"] == len(small_body)

    def test_chunked_body_exactly_1mb_accepted(self, client: TestClient) -> None:
        """Bytes body == 1 MB exactly → accepted via Phase 1 (boundary condition).

        Despite the method name, TestClient injects Content-Length for bytes
        bodies, so this test exercises Phase 1 (not Phase 2 rolling cap).
        """
        exact_body = b"z" * MAX_REQUEST_BODY_BYTES
        response = client.post(
            "/upload",
            content=exact_body,
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200

    def test_get_request_with_no_body_accepted(self, client: TestClient) -> None:
        """GET with no body passes through the middleware without issue."""
        async def _ping(request: Request) -> Response:
            return JSONResponse({"pong": True})

        app2 = Starlette(routes=[Route("/ping", _ping, methods=["GET"])])
        app2.add_middleware(BodySizeLimitMiddleware)
        tc = TestClient(app2)
        resp = tc.get("/ping")
        assert resp.status_code == 200


# ─── Genuine Phase 2: generator bodies (no Content-Length) ───────────────────


class TestChunkedPhase2:
    """Phase 2: Chunked bodies with no Content-Length — rolling 1 MB cap (AC-7).

    Uses generator bodies so that ``requests`` does NOT inject a
    ``Content-Length`` header.  Without that header the middleware skips
    Phase 1 and routes through Phase 2 (the ``async for chunk in
    request.stream()`` rolling accumulation path).

    This is the only test class that provides genuine Phase 2 coverage.
    """

    def test_chunked_generator_over_1mb_returns_413(
        self, client: TestClient
    ) -> None:
        """Generator body > 1 MB → HTTP 413 via Phase 2 rolling cap (AC-7)."""
        total = MAX_REQUEST_BODY_BYTES + 1
        chunk_size = 65_536

        def oversized_body():
            sent = 0
            while sent < total:
                size = min(chunk_size, total - sent)
                yield b"x" * size
                sent += size

        response = client.post(
            "/upload",
            content=oversized_body(),
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 413
        body = response.json()
        assert body["error"]["code"] == "payload_too_large"
        assert "1MB" in body["error"]["message"]

    def test_chunked_generator_within_limit_is_accepted(
        self, client: TestClient
    ) -> None:
        """Generator body ≤ 1 MB → accepted via Phase 2 rolling cap (AC-7)."""
        total = MAX_REQUEST_BODY_BYTES // 2  # 512 KB — well within the limit
        chunk_size = 65_536

        def small_body():
            sent = 0
            while sent < total:
                size = min(chunk_size, total - sent)
                yield b"y" * size
                sent += size

        response = client.post(
            "/upload",
            content=small_body(),
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["received_bytes"] == total


# ─── Middleware does not interfere with normal-sized requests ─────────────────


class TestNormalRequests:
    """Middleware transparency: normal requests pass through unmodified."""

    def test_typical_llm_request_body_passes(self, client: TestClient) -> None:
        """A typical LLM request body (a few KB) should pass without modification."""
        import json

        payload = json.dumps(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello!"}],
            }
        ).encode()
        response = client.post(
            "/upload",
            content=payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["received_bytes"] == len(payload)

    def test_100kb_body_passes(self, client: TestClient) -> None:
        """100 KB body (well under limit) passes through."""
        body = b"a" * (100 * 1024)
        response = client.post(
            "/upload",
            content=body,
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        assert response.json()["received_bytes"] == len(body)
