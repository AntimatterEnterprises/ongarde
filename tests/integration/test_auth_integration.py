"""Integration tests for E-006-S-004: authenticate_request in proxy pipeline.

Tests verify:
  - 401 before scan (AC-E006-08): scanner not called on auth failure
  - Header stripping works end-to-end
  - Full proxy flow with valid key
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.keys import clear_key_cache, create_api_key, init_key_store
from app.audit.protocol import NullAuditBackend
from app.main import create_app


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def require_auth_mode(monkeypatch):
    """Set ONGARDE_AUTH_REQUIRED=true for all tests in this module."""
    monkeypatch.setenv("ONGARDE_AUTH_REQUIRED", "true")


@pytest.fixture
async def app_with_keys(tmp_path: Path):
    """Create a running app with a pre-initialized key store."""
    db = tmp_path / "keys.db"
    await init_key_store(db)
    clear_key_cache()

    import os
    os.environ["ONGARDE_KEYS_DB_PATH"] = str(db)

    application = create_app()

    # Fast-track startup: set state directly without running lifespan
    import httpx
    from app.config import load_config
    from app.utils.health import ScanLatencyTracker

    config = load_config()
    application.state.config = config
    application.state.audit_backend = NullAuditBackend()
    application.state.scan_pool = None
    application.state.http_client = httpx.AsyncClient()
    application.state.latency_tracker = ScanLatencyTracker()
    application.state.ready = True

    yield application, db

    # Cleanup
    await application.state.http_client.aclose()
    if "ONGARDE_KEYS_DB_PATH" in os.environ:
        del os.environ["ONGARDE_KEYS_DB_PATH"]


class TestAuthBeforeScan:
    """AC-E006-08: 401 must be returned before the scanner is invoked."""

    async def test_no_key_returns_401(self, app_with_keys) -> None:
        """Request with no key returns 401 immediately."""
        app, db = app_with_keys

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            )

        assert response.status_code == 401

    async def test_invalid_key_returns_401(self, app_with_keys) -> None:
        """Request with invalid key returns 401."""
        app, db = app_with_keys
        clear_key_cache()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                headers={"X-OnGarde-Key": "ong-00000000XXXXXXXXXXXXXXXXXX"},
            )

        assert response.status_code == 401

    async def test_401_before_scan_scanner_never_called(self, app_with_keys) -> None:
        """AC-E006-08: Scanner (scan_or_block) is never called when 401 is returned."""
        app, db = app_with_keys
        scan_calls = []

        async def mock_scan(content, scan_pool, scan_id, audit_context):
            scan_calls.append(True)
            from app.models.scan import Action, RiskLevel, ScanResult
            return ScanResult(action=Action.ALLOW, scan_id=scan_id)

        with patch("app.proxy.engine.scan_or_block", new=mock_scan):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": "test"}]},
                    # No key!
                )

        assert response.status_code == 401
        assert len(scan_calls) == 0, (
            f"Scanner was called {len(scan_calls)} time(s) before auth failure! "
            "AC-E006-08 violated: 401 must precede any scan."
        )

    async def test_valid_key_reaches_scan_gate(self, app_with_keys) -> None:
        """With a valid key, the scan gate IS invoked (scanner called)."""
        app, db = app_with_keys
        clear_key_cache()

        plaintext, _ = await create_api_key("user1", db)
        scan_calls = []

        async def mock_scan(content, scan_pool, scan_id, audit_context):
            scan_calls.append(True)
            from app.models.scan import Action, RiskLevel, ScanResult
            return ScanResult(action=Action.ALLOW, scan_id=scan_id)

        # Mock both scan AND upstream so we don't need a real LLM
        import httpx
        from fastapi.responses import JSONResponse

        async def mock_upstream(request, **kwargs):
            return httpx.Response(
                200,
                content=b'{"id":"test","choices":[]}',
                headers={"content-type": "application/json"},
            )

        with patch("app.proxy.engine.scan_or_block", new=mock_scan):
            with patch.object(app.state.http_client, "send", new=mock_upstream):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o",
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                        headers={"X-OnGarde-Key": plaintext},
                    )

        assert len(scan_calls) == 1, "Scanner should have been called once with a valid key"


class TestHeaderStripping:
    """Verify X-OnGarde-Key is stripped from upstream-bound requests."""

    async def test_x_ongarde_key_stripped(self, app_with_keys) -> None:
        """X-OnGarde-Key header is NOT forwarded to upstream LLM."""
        app, db = app_with_keys
        clear_key_cache()
        plaintext, _ = await create_api_key("user1", db)

        captured_headers = {}

        async def capture_upstream(request, **kwargs):
            captured_headers.update(dict(request.headers))
            return httpx.Response(
                200,
                content=b'{"id":"t","choices":[]}',
                headers={"content-type": "application/json"},
            )

        import httpx

        async def mock_scan(content, scan_pool, scan_id, audit_context):
            from app.models.scan import Action, ScanResult
            return ScanResult(action=Action.ALLOW, scan_id=scan_id)

        with patch("app.proxy.engine.scan_or_block", new=mock_scan):
            with patch.object(app.state.http_client, "send", new=capture_upstream):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await client.post(
                        "/v1/chat/completions",
                        json={"model": "gpt-4", "messages": []},
                        headers={"X-OnGarde-Key": plaintext},
                    )

        # X-OnGarde-Key must not be present in upstream headers
        upstream_header_names = {k.lower() for k in captured_headers}
        assert "x-ongarde-key" not in upstream_header_names, (
            "X-OnGarde-Key was forwarded to upstream — it must be stripped!"
        )
