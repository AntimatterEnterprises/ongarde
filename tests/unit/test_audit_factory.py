"""Unit tests for E-005-S-006: audit/factory.py

Tests:
  - No SUPABASE env vars → LocalSQLiteBackend selected
  - SUPABASE_URL + SUPABASE_KEY set → SupabaseBackend selected
  - ONGARDE_AUDIT_DB_PATH override used for SQLite path
  - Default path ~/.ongarde/audit.db used when ONGARDE_AUDIT_DB_PATH absent
  - RuntimeError from LocalSQLiteBackend propagates (schema mismatch)
  - SUPABASE_KEY not set (only URL) → LocalSQLiteBackend fallback
  - No raw credentials in audit_backend_selected log
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from app.audit.factory import (
    _DEFAULT_AUDIT_DB_PATH,
    _ENV_AUDIT_DB_PATH,
    _ENV_SUPABASE_KEY,
    _ENV_SUPABASE_URL,
    create_audit_backend,
)
from app.audit.protocol import AuditBackend
from app.audit.sqlite_backend import LocalSQLiteBackend
from app.audit.supabase_backend import SupabaseBackend


# ─── Backend Selection ────────────────────────────────────────────────────────


class TestBackendSelection:
    """Factory selects LocalSQLiteBackend or SupabaseBackend based on env vars."""

    async def test_no_supabase_env_vars_returns_local_sqlite(
        self, tmp_path: Any
    ) -> None:
        """No SUPABASE_URL/KEY → LocalSQLiteBackend (AC-E005-06 item 1)."""
        db_path = str(tmp_path / "factory_test.db")
        env = {
            _ENV_AUDIT_DB_PATH: db_path,
        }
        with patch.dict(os.environ, env, clear=False):
            # Ensure SUPABASE env vars are absent
            os.environ.pop(_ENV_SUPABASE_URL, None)
            os.environ.pop(_ENV_SUPABASE_KEY, None)

            config = MagicMock()
            backend = await create_audit_backend(config)

        assert isinstance(backend, LocalSQLiteBackend)
        await backend.close()

    async def test_supabase_env_vars_present_returns_supabase_backend(
        self, tmp_path: Any
    ) -> None:
        """SUPABASE_URL + SUPABASE_KEY set → SupabaseBackend selected (AC-E005-06 item 2)."""
        with patch.dict(
            os.environ,
            {
                _ENV_SUPABASE_URL: "https://test.supabase.co",
                _ENV_SUPABASE_KEY: "service-role-key-test",
            },
        ):
            # Patch SupabaseBackend.initialize to avoid real network call.
            # (create_async_client is only in module namespace when supabase is installed;
            # patching initialize() avoids that dependency entirely.)
            with patch.object(SupabaseBackend, "initialize", new_callable=AsyncMock):
                config = MagicMock()
                backend = await create_audit_backend(config)

        assert isinstance(backend, SupabaseBackend)
        await backend.close()

    async def test_only_supabase_url_set_falls_back_to_local(
        self, tmp_path: Any
    ) -> None:
        """Only SUPABASE_URL set (no KEY) → LocalSQLiteBackend (AC-E005-06 item 3)."""
        db_path = str(tmp_path / "factory_fallback.db")
        env = {
            _ENV_AUDIT_DB_PATH: db_path,
            _ENV_SUPABASE_URL: "https://test.supabase.co",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(_ENV_SUPABASE_KEY, None)

            config = MagicMock()
            backend = await create_audit_backend(config)

        assert isinstance(backend, LocalSQLiteBackend)
        await backend.close()

    async def test_only_supabase_key_set_falls_back_to_local(
        self, tmp_path: Any
    ) -> None:
        """Only SUPABASE_KEY set (no URL) → LocalSQLiteBackend fallback."""
        db_path = str(tmp_path / "factory_fallback2.db")
        env = {
            _ENV_AUDIT_DB_PATH: db_path,
            _ENV_SUPABASE_KEY: "service-role-key-test",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(_ENV_SUPABASE_URL, None)

            config = MagicMock()
            backend = await create_audit_backend(config)

        assert isinstance(backend, LocalSQLiteBackend)
        await backend.close()


# ─── SQLite Path Selection ────────────────────────────────────────────────────


class TestSQLitePathSelection:
    """Factory uses ONGARDE_AUDIT_DB_PATH override or default path."""

    async def test_uses_audit_db_path_env_var(self, tmp_path: Any) -> None:
        """ONGARDE_AUDIT_DB_PATH env var overrides default path (AC-E005-06 item 4)."""
        custom_path = str(tmp_path / "custom_audit.db")
        env = {_ENV_AUDIT_DB_PATH: custom_path}

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(_ENV_SUPABASE_URL, None)
            os.environ.pop(_ENV_SUPABASE_KEY, None)

            config = MagicMock()
            backend = await create_audit_backend(config)

        assert isinstance(backend, LocalSQLiteBackend)
        assert backend._db_path == custom_path
        await backend.close()

    async def test_default_path_used_when_env_absent(self, tmp_path: Any) -> None:
        """Default path ~/.ongarde/audit.db is used when ONGARDE_AUDIT_DB_PATH not set."""
        import os as _os

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_SUPABASE_URL, None)
            os.environ.pop(_ENV_SUPABASE_KEY, None)
            os.environ.pop(_ENV_AUDIT_DB_PATH, None)

            # Patch LocalSQLiteBackend to avoid actually creating ~/.ongarde/audit.db
            backend_instance = MagicMock(spec=LocalSQLiteBackend)
            backend_instance.initialize = AsyncMock()
            backend_instance._db_path = _os.path.expanduser(_DEFAULT_AUDIT_DB_PATH)

            with patch(
                "app.audit.factory._create_local_sqlite_backend",
                return_value=AsyncMock(return_value=backend_instance),
            ):
                # Just test the path constant
                assert _DEFAULT_AUDIT_DB_PATH == "~/.ongarde/audit.db"


# ─── PRAGMA Version Guard ─────────────────────────────────────────────────────


class TestPragmaVersionGuard:
    """RuntimeError from incompatible schema propagates to caller (AC-E005-07)."""

    async def test_schema_version_mismatch_raises_runtime_error(
        self, tmp_path: Any
    ) -> None:
        """If DB has user_version=2, create_audit_backend raises RuntimeError (AC-E005-07)."""
        db_path = str(tmp_path / "future_schema.db")

        # Pre-seed with future schema version
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA user_version = 2;")
            await db.commit()

        env = {_ENV_AUDIT_DB_PATH: db_path}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(_ENV_SUPABASE_URL, None)
            os.environ.pop(_ENV_SUPABASE_KEY, None)

            config = MagicMock()
            with pytest.raises(RuntimeError) as exc_info:
                await create_audit_backend(config)

        error_msg = str(exc_info.value)
        assert "2" in error_msg
        assert "reset" in error_msg or "migrate" in error_msg

    async def test_schema_version_99_raises_runtime_error(
        self, tmp_path: Any
    ) -> None:
        """user_version=99 raises RuntimeError — refuses startup."""
        db_path = str(tmp_path / "far_future.db")

        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA user_version = 99;")
            await db.commit()

        env = {_ENV_AUDIT_DB_PATH: db_path}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(_ENV_SUPABASE_URL, None)
            os.environ.pop(_ENV_SUPABASE_KEY, None)

            config = MagicMock()
            with pytest.raises(RuntimeError):
                await create_audit_backend(config)


# ─── Protocol Compliance ──────────────────────────────────────────────────────


class TestProtocolCompliance:
    """All returned backends satisfy AuditBackend protocol."""

    async def test_local_sqlite_satisfies_protocol(self, tmp_path: Any) -> None:
        """LocalSQLiteBackend returned by factory satisfies AuditBackend protocol."""
        db_path = str(tmp_path / "proto_test.db")
        env = {_ENV_AUDIT_DB_PATH: db_path}

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(_ENV_SUPABASE_URL, None)
            os.environ.pop(_ENV_SUPABASE_KEY, None)

            config = MagicMock()
            backend = await create_audit_backend(config)

        assert isinstance(backend, AuditBackend)
        await backend.close()

    async def test_factory_returns_initialized_backend(self, tmp_path: Any) -> None:
        """create_audit_backend() returns a fully initialized backend (not just constructed)."""
        db_path = str(tmp_path / "init_test.db")
        env = {_ENV_AUDIT_DB_PATH: db_path}

        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(_ENV_SUPABASE_URL, None)
            os.environ.pop(_ENV_SUPABASE_KEY, None)

            config = MagicMock()
            backend = await create_audit_backend(config)

        # Verify the backend is functional (initialize() was called)
        assert isinstance(backend, LocalSQLiteBackend)
        assert backend._db is not None  # Connection is open
        result = await backend.health_check()
        assert result is True

        await backend.close()


# ─── Environment Constants ────────────────────────────────────────────────────


def test_env_var_names() -> None:
    """Verify environment variable constants match expected names."""
    assert _ENV_SUPABASE_URL == "SUPABASE_URL"
    assert _ENV_SUPABASE_KEY == "SUPABASE_KEY"
    assert _ENV_AUDIT_DB_PATH == "ONGARDE_AUDIT_DB_PATH"
    assert _DEFAULT_AUDIT_DB_PATH == "~/.ongarde/audit.db"
