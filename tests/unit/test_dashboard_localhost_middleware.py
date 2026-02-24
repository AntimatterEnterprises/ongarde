"""Tests for DashboardLocalhostMiddleware — SEC-002.

Verifies that /dashboard/* routes are blocked for non-localhost origins
and allowed for loopback addresses.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.dashboard.middleware import DashboardLocalhostMiddleware

pytestmark = pytest.mark.asyncio


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DashboardLocalhostMiddleware)

    @app.get("/dashboard/api/status")
    async def dashboard_status():
        return {"status": "ok"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture(autouse=True)
def enable_localhost_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-enable the localhost check for these tests (overrides conftest default)."""
    monkeypatch.setenv("ONGARDE_DASHBOARD_LOCALHOST_ONLY", "true")


class TestDashboardLocalhostMiddleware:

    async def test_localhost_ipv4_allowed(self) -> None:
        """127.0.0.1 origin is allowed through to dashboard routes."""
        app = _make_app()
        transport = ASGITransport(app=app, client=("127.0.0.1", 9999))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/dashboard/api/status")
        assert response.status_code == 200

    async def test_external_ip_blocked(self) -> None:
        """External IP origin receives HTTP 403 for dashboard routes."""
        app = _make_app()
        transport = ASGITransport(app=app, client=("192.168.1.100", 9999))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/dashboard/api/status")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    async def test_public_ip_blocked(self) -> None:
        """Public IP origin receives HTTP 403 for dashboard routes."""
        app = _make_app()
        transport = ASGITransport(app=app, client=("203.0.113.1", 9999))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/dashboard/api/status")
        assert response.status_code == 403

    async def test_non_dashboard_route_unaffected(self) -> None:
        """Non-dashboard routes are not blocked even from external IPs."""
        app = _make_app()
        transport = ASGITransport(app=app, client=("203.0.113.1", 9999))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200

    async def test_check_disabled_allows_external(self, monkeypatch) -> None:
        """When ONGARDE_DASHBOARD_LOCALHOST_ONLY=false, external IPs pass through."""
        monkeypatch.setenv("ONGARDE_DASHBOARD_LOCALHOST_ONLY", "false")
        app = _make_app()
        transport = ASGITransport(app=app, client=("10.0.0.1", 9999))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/dashboard/api/status")
        assert response.status_code == 200
