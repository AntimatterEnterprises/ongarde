"""Integration tests for E-006-S-005: Dashboard API endpoints.

Full flow test: create → list → rotate → list (masked) → revoke → list (empty).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.keys import clear_key_cache, create_api_key, init_key_store
from app.audit.protocol import NullAuditBackend
from app.auth.middleware import authenticate_request
from app.auth.router import router as auth_router
from fastapi import FastAPI, Request


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def auth_app(tmp_path: Path):
    """Minimal FastAPI app with auth router + real key store."""
    db = tmp_path / "keys.db"
    await init_key_store(db)
    clear_key_cache()

    os.environ["ONGARDE_KEYS_DB_PATH"] = str(db)

    app = FastAPI()
    app.state.audit_backend = NullAuditBackend()
    app.include_router(auth_router, prefix="/dashboard/api")

    yield app, db

    if "ONGARDE_KEYS_DB_PATH" in os.environ:
        del os.environ["ONGARDE_KEYS_DB_PATH"]
    clear_key_cache()


@pytest.fixture
async def auth_app_with_key(auth_app):
    """App + a pre-created key, with the app configured to authenticate as that key."""
    app, db = auth_app
    clear_key_cache()
    plaintext, _ = await create_api_key("user1", db)

    # Override authenticate_request to always authenticate as user1
    async def mock_auth(request: Request) -> str:
        return "user1"

    app.dependency_overrides[authenticate_request] = mock_auth

    yield app, db, plaintext


class TestFullKeyManagementFlow:
    """End-to-end key management flow via dashboard API."""

    async def test_get_keys_returns_masked(self, auth_app_with_key) -> None:
        """GET /keys returns masked keys (no plaintext)."""
        app, db, plaintext = auth_app_with_key

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/dashboard/api/keys")

        assert response.status_code == 200
        data = response.json()
        assert len(data["keys"]) == 1
        key_entry = data["keys"][0]
        assert key_entry["masked_key"].startswith("ong-...")
        assert plaintext not in response.text, "Plaintext found in GET /keys response!"

    async def test_rotate_returns_new_plaintext_once(self, auth_app_with_key) -> None:
        """POST /keys/rotate returns new_key in response exactly once."""
        app, db, plaintext = auth_app_with_key

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Get key_id from list
            list_response = await client.get("/dashboard/api/keys")
            key_id = list_response.json()["keys"][0]["id"]

            # Rotate
            rotate_response = await client.post(
                "/dashboard/api/keys/rotate",
                json={"key_id": key_id},
            )

        assert rotate_response.status_code == 200
        data = rotate_response.json()
        assert "new_key" in data
        assert data["new_key"].startswith("ong-")
        assert data["new_key"] != plaintext  # new key differs from old
        assert "not be shown again" in data["message"]

    async def test_rotate_then_get_shows_masked_only(self, auth_app_with_key) -> None:
        """After rotation, GET /keys shows masked form only (no plaintext)."""
        app, db, plaintext = auth_app_with_key

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Get key_id
            list_resp = await client.get("/dashboard/api/keys")
            key_id = list_resp.json()["keys"][0]["id"]

            # Rotate
            rotate_resp = await client.post(
                "/dashboard/api/keys/rotate",
                json={"key_id": key_id},
            )
            new_key = rotate_resp.json()["new_key"]

            # List again — must not contain the new plaintext
            list_after = await client.get("/dashboard/api/keys")

        assert list_after.status_code == 200
        assert new_key not in list_after.text, (
            "New plaintext key found in GET /keys after rotation — should be masked only!"
        )

    async def test_revoke_removes_key(self, auth_app_with_key) -> None:
        """POST /keys/revoke removes key from listing."""
        app, db, plaintext = auth_app_with_key

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Get key_id
            list_resp = await client.get("/dashboard/api/keys")
            key_id = list_resp.json()["keys"][0]["id"]

            # Revoke
            revoke_resp = await client.post(
                "/dashboard/api/keys/revoke",
                json={"key_id": key_id},
            )
            assert revoke_resp.status_code == 200

            # List again — should be empty
            list_after = await client.get("/dashboard/api/keys")

        assert list_after.json()["keys"] == []

    async def test_revoke_nonexistent_key_404(self, auth_app_with_key) -> None:
        """Revoking a non-existent key returns 404."""
        app, db, plaintext = auth_app_with_key

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/dashboard/api/keys/revoke",
                json={"key_id": "NOTFOUND"},
            )

        assert response.status_code == 404
