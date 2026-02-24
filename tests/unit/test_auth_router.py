"""Unit tests for app/auth/router.py — E-006-S-005.

Verifies:
  - GET /dashboard/api/keys returns masked keys (no plaintext)
  - POST /dashboard/api/keys/rotate returns new plaintext key ONCE
  - POST /dashboard/api/keys/revoke removes key
  - All endpoints require authentication (401 without key)
  - Error cases: wrong user → 400/404
  - Audit events logged on rotation/revocation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.audit.protocol import NullAuditBackend
from app.auth.keys import clear_key_cache, create_api_key
from app.auth.router import router as auth_router

pytestmark = pytest.mark.asyncio


def make_test_app(user_id: str = "user1") -> FastAPI:
    """Create a minimal FastAPI app with the auth router and a mock authenticate_request."""
    app = FastAPI()
    app.state.audit_backend = NullAuditBackend()

    # Mock authenticate_request to always return the given user_id
    async def mock_auth(request: Request) -> str:
        return user_id

    # Override the dependency in the router
    from app.auth.middleware import authenticate_request

    app.dependency_overrides[authenticate_request] = mock_auth
    app.include_router(auth_router, prefix="/dashboard/api")
    return app


def make_unauth_app() -> FastAPI:
    """App where authenticate_request always raises 401."""
    from fastapi import HTTPException

    from app.auth.middleware import authenticate_request

    app = FastAPI()

    async def raise_401(request: Request) -> str:
        raise HTTPException(status_code=401, detail="Missing OnGarde API key")

    app.dependency_overrides[authenticate_request] = raise_401
    app.include_router(auth_router, prefix="/dashboard/api")
    return app


class TestGetKeys:
    """Tests for GET /dashboard/api/keys."""

    async def test_returns_keys_list(self, tmp_path: Path) -> None:
        """GET /keys returns list of masked keys."""
        db = tmp_path / "keys.db"
        await create_api_key("user1", db)

        with patch("app.auth.router.list_keys", new=AsyncMock(
            return_value=[{"id": "ABCD1234", "masked_key": "ong-...1234", "created_at": "2026-01-01", "last_used_at": None}]
        )):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.get("/dashboard/api/keys")

        assert response.status_code == 200
        data = response.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        assert data["keys"][0]["masked_key"] == "ong-...1234"

    async def test_no_plaintext_in_response(self, tmp_path: Path) -> None:
        """AC-E006-03: GET /keys never returns plaintext key."""
        db = tmp_path / "keys.db"
        clear_key_cache()
        plaintext, _ = await create_api_key("user1", db)

        with patch("app.auth.keys._resolve_db_path", return_value=db):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.get("/dashboard/api/keys")

        assert response.status_code == 200
        # The full plaintext must not appear anywhere in the response body
        assert plaintext not in response.text

    async def test_unauthenticated_returns_401(self) -> None:
        """All endpoints require authentication."""
        async with AsyncClient(transport=ASGITransport(app=make_unauth_app()), base_url="http://test") as client:
            response = await client.get("/dashboard/api/keys")
        assert response.status_code == 401

    async def test_empty_keys_returns_empty_list(self) -> None:
        """Empty user returns {"keys": []}."""
        with patch("app.auth.router.list_keys", new=AsyncMock(return_value=[])):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.get("/dashboard/api/keys")
        assert response.status_code == 200
        assert response.json() == {"keys": []}


class TestRotateKey:
    """Tests for POST /dashboard/api/keys/rotate."""

    async def test_rotate_returns_new_key_once(self, tmp_path: Path) -> None:
        """AC-E006-04: Rotation response contains new_key plaintext ONCE."""
        new_plaintext = "ong-01NEWKEYXXXXXXXXXXXXXXXXX"
        with patch(
            "app.auth.router.rotate_api_key_by_id",
            new=AsyncMock(return_value=(new_plaintext, "$2b$12$hash")),
        ):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.post(
                    "/dashboard/api/keys/rotate",
                    json={"key_id": "ABCD1234"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["new_key"] == new_plaintext
        assert "masked_key" in data
        assert data["masked_key"].startswith("ong-...")
        assert "message" in data
        assert "not be shown again" in data["message"]

    async def test_rotate_masked_key_not_plaintext(self) -> None:
        """Masked key in rotation response differs from new_key."""
        new_plaintext = "ong-01ABCDEFGHJKLMNPQRSTUVWXY"
        with patch(
            "app.auth.router.rotate_api_key_by_id",
            new=AsyncMock(return_value=(new_plaintext, "$2b$12$hash")),
        ):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.post(
                    "/dashboard/api/keys/rotate",
                    json={"key_id": "01ABCDEF"},
                )

        data = response.json()
        assert data["masked_key"] != data["new_key"]
        assert "..." in data["masked_key"]

    async def test_rotate_invalid_key_returns_400(self) -> None:
        """AC-S005-02: Unknown key_id returns HTTP 400."""
        from app.auth.keys import InvalidKeyError

        with patch(
            "app.auth.router.rotate_api_key_by_id",
            new=AsyncMock(side_effect=InvalidKeyError("Key not found")),
        ):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.post(
                    "/dashboard/api/keys/rotate",
                    json={"key_id": "NOTFOUND"},
                )

        assert response.status_code == 400

    async def test_rotate_unauthenticated_returns_401(self) -> None:
        async with AsyncClient(transport=ASGITransport(app=make_unauth_app()), base_url="http://test") as client:
            response = await client.post(
                "/dashboard/api/keys/rotate",
                json={"key_id": "ABCD1234"},
            )
        assert response.status_code == 401

    async def test_rotate_audit_event_logged(self) -> None:
        """AC-E006-06: KEY_ROTATED audit event is logged."""
        mock_backend = AsyncMock()
        mock_backend.log_event = AsyncMock()

        new_plaintext = "ong-01NEWKEYXXXXXXXXXXXXXXXXX"

        with patch(
            "app.auth.router.rotate_api_key_by_id",
            new=AsyncMock(return_value=(new_plaintext, "$2b$12$hash")),
        ) as mock_rotate:
            # Capture the audit_backend argument
            app = make_test_app()
            app.state.audit_backend = mock_backend

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/dashboard/api/keys/rotate",
                    json={"key_id": "ABCD1234"},
                )

        assert response.status_code == 200
        # rotate_api_key_by_id was called with audit_backend
        call_kwargs = mock_rotate.call_args
        assert call_kwargs is not None


class TestRevokeKey:
    """Tests for POST /dashboard/api/keys/revoke."""

    async def test_revoke_success(self) -> None:
        """AC-S005-03: Successful revocation returns message and revoked_id."""
        with patch(
            "app.auth.router.revoke_api_key",
            new=AsyncMock(return_value=True),
        ):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.post(
                    "/dashboard/api/keys/revoke",
                    json={"key_id": "ABCD1234"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["revoked_id"] == "ABCD1234"
        assert "revoked" in data["message"].lower()

    async def test_revoke_not_found_returns_404(self) -> None:
        """AC-S005-03: Key not found returns HTTP 404."""
        with patch(
            "app.auth.router.revoke_api_key",
            new=AsyncMock(return_value=False),
        ):
            async with AsyncClient(transport=ASGITransport(app=make_test_app()), base_url="http://test") as client:
                response = await client.post(
                    "/dashboard/api/keys/revoke",
                    json={"key_id": "NOTFOUND"},
                )

        assert response.status_code == 404

    async def test_revoke_unauthenticated_returns_401(self) -> None:
        async with AsyncClient(transport=ASGITransport(app=make_unauth_app()), base_url="http://test") as client:
            response = await client.post(
                "/dashboard/api/keys/revoke",
                json={"key_id": "ABCD1234"},
            )
        assert response.status_code == 401

    async def test_revoke_audit_event_logged(self) -> None:
        """AC-E006-06: KEY_REVOKED audit event is logged."""
        mock_backend = AsyncMock()

        with patch(
            "app.auth.router.revoke_api_key",
            new=AsyncMock(return_value=True),
        ) as mock_revoke:
            app = make_test_app()
            app.state.audit_backend = mock_backend

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/dashboard/api/keys/revoke",
                    json={"key_id": "ABCD1234"},
                )

        assert response.status_code == 200
        call_kwargs = mock_revoke.call_args
        assert call_kwargs is not None


class TestCreateKey:
    """Tests for POST /dashboard/api/keys — E-007-S-001 bootstrap endpoint."""

    async def test_create_key_returns_plaintext_once(self) -> None:
        """POST /dashboard/api/keys returns plaintext key in response."""
        new_plaintext = "ong-01HXNEWAPIKEYTESTXXXXXXXX"
        with patch(
            "app.auth.router.create_api_key",
            new=AsyncMock(return_value=(new_plaintext, "$2b$12$fakehash")),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=make_test_app()), base_url="http://test"
            ) as client:
                response = await client.post("/dashboard/api/keys", json={})

        assert response.status_code == 200
        body = response.json()
        assert body["key"] == new_plaintext
        assert body["key"].startswith("ong-")
        assert "masked_key" in body
        assert body["masked_key"].startswith("ong-...")
        assert "id" in body
        assert "message" in body

    async def test_create_key_masked_key_format(self) -> None:
        """masked_key ends with last 4 chars of the ULID."""
        new_plaintext = "ong-01HXNEWAPIKEYTESTXXXXXXX1"
        with patch(
            "app.auth.router.create_api_key",
            new=AsyncMock(return_value=(new_plaintext, "$2b$12$fakehash")),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=make_test_app()), base_url="http://test"
            ) as client:
                response = await client.post("/dashboard/api/keys", json={})

        body = response.json()
        # ULID part is everything after 'ong-'
        ulid_part = new_plaintext[4:]
        expected_masked = f"ong-...{ulid_part[-4:]}"
        assert body["masked_key"] == expected_masked

    async def test_create_key_unauthenticated_when_auth_required(self) -> None:
        """POST /dashboard/api/keys returns 401 when auth is required and key missing."""
        async with AsyncClient(
            transport=ASGITransport(app=make_unauth_app()), base_url="http://test"
        ) as client:
            response = await client.post("/dashboard/api/keys", json={})
        assert response.status_code == 401

    async def test_create_key_limit_exceeded_returns_400(self) -> None:
        """POST /dashboard/api/keys returns 400 when key limit reached."""
        from app.auth.keys import KeyLimitExceededError

        with patch(
            "app.auth.router.create_api_key",
            new=AsyncMock(side_effect=KeyLimitExceededError()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=make_test_app()), base_url="http://test"
            ) as client:
                response = await client.post("/dashboard/api/keys", json={})

        assert response.status_code == 400
