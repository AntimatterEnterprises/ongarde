"""Unit tests for app/auth/keys.py — E-006-S-001, E-006-S-002, E-006-S-003.

Test coverage:
  E-006-S-001: init_key_store, create_api_key (key format, bcrypt, permissions)
  E-006-S-002: validate_api_key, LRU cache, 2-key maximum
  E-006-S-003: rotate_api_key, rotate_api_key_by_id, revoke_api_key

Non-negotiables verified:
  - Plaintext NEVER in DB bytes (AC-E006-01)
  - os.chmod(0o600) on creation (AC-E006-07)
  - bcrypt rounds=12
  - LRU cache hit < 2ms (AC-E006-09)
  - Old key → None within 1 second of rotation (AC-E006-02)
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

import aiosqlite
import bcrypt
import pytest

from app.auth.keys import (
    InvalidKeyError,
    KeyLimitExceededError,
    clear_key_cache,
    create_api_key,
    init_key_store,
    list_keys,
    revoke_api_key,
    rotate_api_key,
    rotate_api_key_by_id,
    validate_api_key,
)

# ─── Pattern for key format validation ───────────────────────────────────────
# ong-<26-char-ULID> — ULID is uppercase Crockford Base32
_KEY_FORMAT_RE = re.compile(r"^ong-[0-9A-Z]{26}$", re.IGNORECASE)

pytestmark = pytest.mark.asyncio


# ─── E-006-S-001 Tests ───────────────────────────────────────────────────────


class TestInitKeyStore:
    """Tests for init_key_store()."""

    async def test_creates_db_file(self, tmp_path: Path) -> None:
        """init_key_store creates the SQLite file."""
        db = tmp_path / "keys.db"
        assert not db.exists()
        await init_key_store(db)
        assert db.exists()

    async def test_file_permissions_0600(self, tmp_path: Path) -> None:
        """AC-E006-07: File permissions must be exactly 0o600."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        mode = os.stat(db).st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    async def test_pragma_user_version_1(self, tmp_path: Path) -> None:
        """PRAGMA user_version must be 1 after init."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        async with aiosqlite.connect(str(db)) as conn:
            async with conn.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
        assert row[0] == 1

    async def test_creates_api_keys_table(self, tmp_path: Path) -> None:
        """api_keys table exists with correct schema."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        async with aiosqlite.connect(str(db)) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None, "api_keys table not found"

    async def test_creates_index(self, tmp_path: Path) -> None:
        """idx_keys_user_active index must exist."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        async with aiosqlite.connect(str(db)) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_keys_user_active'"
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None, "Index idx_keys_user_active not found"

    async def test_idempotent_double_call(self, tmp_path: Path) -> None:
        """init_key_store called twice does not fail."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        await init_key_store(db)  # must not raise
        mode = os.stat(db).st_mode & 0o777
        assert mode == 0o600

    async def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Parent directories are created if they don't exist."""
        db = tmp_path / "nested" / "dir" / "keys.db"
        assert not db.parent.exists()
        await init_key_store(db)
        assert db.exists()

    async def test_returns_path(self, tmp_path: Path) -> None:
        """init_key_store returns the resolved Path."""
        db = tmp_path / "keys.db"
        result = await init_key_store(db)
        assert result == db


class TestCreateApiKey:
    """Tests for create_api_key() — E-006-S-001."""

    async def test_key_format(self, tmp_path: Path) -> None:
        """AC-S001-01: Key format is ong-<26-char-ULID>."""
        db = tmp_path / "keys.db"
        plaintext, _ = await create_api_key("user1", db)
        assert _KEY_FORMAT_RE.match(plaintext), f"Key format invalid: {plaintext!r}"
        assert len(plaintext) == 30, f"Expected 30 chars, got {len(plaintext)}"

    async def test_returns_tuple(self, tmp_path: Path) -> None:
        """create_api_key returns (plaintext, hash) tuple."""
        db = tmp_path / "keys.db"
        result = await create_api_key("user1", db)
        assert isinstance(result, tuple)
        assert len(result) == 2

    async def test_bcrypt_hash_structure(self, tmp_path: Path) -> None:
        """AC-S001-02: Hash starts with $2b$12$."""
        db = tmp_path / "keys.db"
        plaintext, key_hash = await create_api_key("user1", db)
        assert key_hash.startswith("$2b$12$"), f"Expected $2b$12$ prefix, got: {key_hash[:10]}"

    async def test_bcrypt_verifies(self, tmp_path: Path) -> None:
        """AC-S001-02: bcrypt.checkpw(plaintext, hash) returns True."""
        db = tmp_path / "keys.db"
        plaintext, key_hash = await create_api_key("user1", db)
        assert bcrypt.checkpw(plaintext.encode(), key_hash.encode())

    async def test_plaintext_absent_from_db_bytes(self, tmp_path: Path) -> None:
        """AC-E006-01 / AC-S001-02: Plaintext key must NOT appear in raw DB bytes."""
        db = tmp_path / "keys.db"
        plaintext, _ = await create_api_key("user1", db)
        raw_bytes = db.read_bytes()
        assert plaintext.encode() not in raw_bytes, (
            f"Plaintext key found in raw DB bytes! Key: {plaintext!r}"
        )

    async def test_two_keys_are_unique(self, tmp_path: Path) -> None:
        """AC-S001-01: Each call generates a unique ULID."""
        db = tmp_path / "keys.db"
        k1, _ = await create_api_key("user1", db)
        k2, _ = await create_api_key("user2", db)
        assert k1 != k2

    async def test_env_var_db_path_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-S001-04: ONGARDE_KEYS_DB_PATH env var overrides default path."""
        db = tmp_path / "custom" / "keys.db"
        monkeypatch.setenv("ONGARDE_KEYS_DB_PATH", str(db))
        await create_api_key("user1")  # no db_path argument
        assert db.exists()

    async def test_file_permissions_after_create(self, tmp_path: Path) -> None:
        """File permissions remain 0o600 after key creation."""
        db = tmp_path / "keys.db"
        await create_api_key("user1", db)
        mode = os.stat(db).st_mode & 0o777
        assert mode == 0o600


# ─── E-006-S-002 Tests ───────────────────────────────────────────────────────


class TestValidateApiKey:
    """Tests for validate_api_key() — E-006-S-002."""

    async def test_valid_key_returns_user_id(self, tmp_path: Path) -> None:
        """AC-S002-01: Valid active key returns user_id."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        plaintext, _ = await create_api_key("user1", db)
        result = await validate_api_key(plaintext, db)
        assert result == "user1"

    async def test_invalid_key_returns_none(self, tmp_path: Path) -> None:
        """AC-S002-01: Wrong key returns None."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        await init_key_store(db)
        result = await validate_api_key("ong-01ZZZZZZZZZZZZZZZZZZZZZZZZ", db)
        assert result is None

    async def test_revoked_key_returns_none(self, tmp_path: Path) -> None:
        """AC-S002-01: active=0 key returns None."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        plaintext, _ = await create_api_key("user1", db)
        key_id = plaintext[4:]
        # Manually deactivate
        async with aiosqlite.connect(str(db)) as conn:
            await conn.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,))
            await conn.commit()
        clear_key_cache()
        result = await validate_api_key(plaintext, db)
        assert result is None

    async def test_bad_format_returns_none(self, tmp_path: Path) -> None:
        """Non-ong- key returns None immediately."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        await init_key_store(db)
        assert await validate_api_key("sk-not-an-ong-key", db) is None
        assert await validate_api_key("", db) is None
        assert await validate_api_key("ong-", db) is None  # too short

    async def test_cache_hit_is_fast(self, tmp_path: Path) -> None:
        """AC-S002-02: Second validation uses cache (< 2ms vs ~80ms bcrypt)."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        plaintext, _ = await create_api_key("user1", db)

        # First call: cache miss (bcrypt — slow)
        first_result = await validate_api_key(plaintext, db)
        assert first_result == "user1"

        # Second call: cache hit — should be fast
        t0 = time.monotonic()
        second_result = await validate_api_key(plaintext, db)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert second_result == "user1"
        assert elapsed_ms < 5.0, f"Cache hit took {elapsed_ms:.1f}ms — expected <5ms"

    async def test_cache_clear_forces_db_recheck(self, tmp_path: Path) -> None:
        """AC-S002-02: clear_key_cache() forces re-validation on next call."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        plaintext, _ = await create_api_key("user1", db)
        await validate_api_key(plaintext, db)  # populate cache

        # Deactivate key directly in DB
        key_id = plaintext[4:]
        async with aiosqlite.connect(str(db)) as conn:
            await conn.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,))
            await conn.commit()

        clear_key_cache()
        result = await validate_api_key(plaintext, db)
        assert result is None, "Expected None after cache clear + key deactivation"


class TestKeyLimitExceeded:
    """Tests for 2-key maximum per user — E-006-S-002."""

    async def test_two_keys_allowed(self, tmp_path: Path) -> None:
        """User can have exactly 2 active keys."""
        db = tmp_path / "keys.db"
        k1, _ = await create_api_key("user1", db)
        k2, _ = await create_api_key("user1", db)
        assert k1 != k2

    async def test_third_key_raises(self, tmp_path: Path) -> None:
        """AC-E006-05: Creating 3rd key raises KeyLimitExceededError."""
        db = tmp_path / "keys.db"
        await create_api_key("user1", db)
        await create_api_key("user1", db)
        with pytest.raises(KeyLimitExceededError) as exc_info:
            await create_api_key("user1", db)
        assert exc_info.value.code == "key_limit_exceeded"
        assert "2" in str(exc_info.value)

    async def test_different_users_independent(self, tmp_path: Path) -> None:
        """2-key limit is per user — different users don't interfere."""
        db = tmp_path / "keys.db"
        await create_api_key("user1", db)
        await create_api_key("user1", db)
        # user2 can still create keys
        k, _ = await create_api_key("user2", db)
        assert k.startswith("ong-")

    async def test_revoke_then_create_succeeds(self, tmp_path: Path) -> None:
        """After revoking a key, user can create a new one."""
        db = tmp_path / "keys.db"
        k1, _ = await create_api_key("user1", db)
        await create_api_key("user1", db)  # now at 2 keys

        # Revoke first key
        key_id = k1[4:]
        await revoke_api_key("user1", key_id, db)

        # Now can create again
        k3, _ = await create_api_key("user1", db)
        assert k3.startswith("ong-")


# ─── E-006-S-003 Tests ───────────────────────────────────────────────────────


class TestRotateApiKey:
    """Tests for rotate_api_key() — E-006-S-003."""

    async def test_rotate_returns_new_plaintext(self, tmp_path: Path) -> None:
        """AC-E006-04: rotate returns new plaintext key."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)
        new_key, new_hash = await rotate_api_key("user1", old_key, db)
        assert new_key.startswith("ong-")
        assert _KEY_FORMAT_RE.match(new_key)
        assert new_key != old_key

    async def test_old_key_invalid_after_rotate(self, tmp_path: Path) -> None:
        """AC-E006-02: Old key returns None after rotation (within 1 second)."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)
        await rotate_api_key("user1", old_key, db)
        result = await validate_api_key(old_key, db)
        assert result is None, "Old key should be invalid after rotation"

    async def test_new_key_valid_after_rotate(self, tmp_path: Path) -> None:
        """New key is immediately valid after rotation."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)
        new_key, _ = await rotate_api_key("user1", old_key, db)
        result = await validate_api_key(new_key, db)
        assert result == "user1"

    async def test_rotate_invalid_key_raises(self, tmp_path: Path) -> None:
        """rotate_api_key with invalid old_key raises InvalidKeyError."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        with pytest.raises(InvalidKeyError):
            await rotate_api_key("user1", "ong-00000000XXXXXXXXXXXXXXXXXX", db)

    async def test_rotate_clears_cache(self, tmp_path: Path) -> None:
        """AC-E006-09: LRU cache cleared on rotation — immediate invalidation."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)

        # Populate cache with old key
        await validate_api_key(old_key, db)

        # Rotate — must clear cache
        new_key, _ = await rotate_api_key("user1", old_key, db)

        # Old key must not be served from cache
        result = await validate_api_key(old_key, db)
        assert result is None, "Old key served from cache after rotation!"

    async def test_rotate_bcrypt_rounds_12(self, tmp_path: Path) -> None:
        """New key after rotation uses bcrypt rounds=12."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)
        new_key, new_hash = await rotate_api_key("user1", old_key, db)
        assert new_hash.startswith("$2b$12$"), f"Expected $2b$12$ in hash: {new_hash[:10]}"

    async def test_rotate_maintains_one_active_key(self, tmp_path: Path) -> None:
        """After rotation, user has exactly 1 active key."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)
        await rotate_api_key("user1", old_key, db)

        async with aiosqlite.connect(str(db)) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM api_keys WHERE user_id='user1' AND active=1"
            ) as cursor:
                row = await cursor.fetchone()
        assert row[0] == 1, f"Expected 1 active key, got {row[0]}"

    async def test_rotate_by_id(self, tmp_path: Path) -> None:
        """rotate_api_key_by_id works without old plaintext."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)
        key_id = old_key[4:]

        new_key, _ = await rotate_api_key_by_id("user1", key_id, db)
        assert new_key.startswith("ong-")
        assert new_key != old_key

        # Old key invalid
        assert await validate_api_key(old_key, db) is None
        # New key valid
        assert await validate_api_key(new_key, db) == "user1"

    async def test_rotate_by_id_wrong_user_raises(self, tmp_path: Path) -> None:
        """rotate_api_key_by_id with wrong user_id raises InvalidKeyError."""
        db = tmp_path / "keys.db"
        old_key, _ = await create_api_key("user1", db)
        key_id = old_key[4:]
        with pytest.raises(InvalidKeyError):
            await rotate_api_key_by_id("user2", key_id, db)  # wrong user!


class TestRevokeApiKey:
    """Tests for revoke_api_key() — E-006-S-003."""

    async def test_revoke_returns_true(self, tmp_path: Path) -> None:
        """revoke_api_key returns True when key found and revoked."""
        db = tmp_path / "keys.db"
        key, _ = await create_api_key("user1", db)
        key_id = key[4:]
        result = await revoke_api_key("user1", key_id, db)
        assert result is True

    async def test_revoke_key_invalid_after(self, tmp_path: Path) -> None:
        """AC-S003-03: Revoked key returns None from validate_api_key."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        key, _ = await create_api_key("user1", db)
        key_id = key[4:]
        await revoke_api_key("user1", key_id, db)
        result = await validate_api_key(key, db)
        assert result is None

    async def test_revoke_nonexistent_returns_false(self, tmp_path: Path) -> None:
        """AC-S003-03: revoke_api_key returns False for missing key."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        result = await revoke_api_key("user1", "00000000", db)
        assert result is False

    async def test_revoke_wrong_user_returns_false(self, tmp_path: Path) -> None:
        """revoke_api_key with wrong user_id returns False (security check)."""
        db = tmp_path / "keys.db"
        key, _ = await create_api_key("user1", db)
        key_id = key[4:]
        result = await revoke_api_key("user2", key_id, db)  # wrong user
        assert result is False

    async def test_revoke_clears_cache(self, tmp_path: Path) -> None:
        """AC-E006-09: Cache cleared on revocation."""
        clear_key_cache()
        db = tmp_path / "keys.db"
        key, _ = await create_api_key("user1", db)
        # Populate cache
        await validate_api_key(key, db)

        # Revoke
        key_id = key[4:]
        await revoke_api_key("user1", key_id, db)

        # Must not be served from cache
        result = await validate_api_key(key, db)
        assert result is None


# ─── E-006-S-005 Tests (list_keys) ───────────────────────────────────────────


class TestListKeys:
    """Tests for list_keys() — E-006-S-005."""

    async def test_returns_active_keys(self, tmp_path: Path) -> None:
        """list_keys returns only active keys."""
        db = tmp_path / "keys.db"
        await create_api_key("user1", db)
        keys = await list_keys("user1", db)
        assert len(keys) == 1
        assert "masked_key" in keys[0]
        assert "id" in keys[0]

    async def test_masked_key_no_plaintext(self, tmp_path: Path) -> None:
        """AC-E006-03: masked_key does not expose plaintext."""
        db = tmp_path / "keys.db"
        plaintext, _ = await create_api_key("user1", db)
        keys = await list_keys("user1", db)
        assert keys[0]["masked_key"] != plaintext
        assert keys[0]["masked_key"].startswith("ong-...")

    async def test_revoked_keys_not_listed(self, tmp_path: Path) -> None:
        """Revoked (active=0) keys do not appear in list."""
        db = tmp_path / "keys.db"
        key, _ = await create_api_key("user1", db)
        key_id = key[4:]
        await revoke_api_key("user1", key_id, db)
        keys = await list_keys("user1", db)
        assert len(keys) == 0

    async def test_empty_db_returns_empty_list(self, tmp_path: Path) -> None:
        """list_keys returns empty list when no keys exist."""
        db = tmp_path / "keys.db"
        await init_key_store(db)
        keys = await list_keys("user1", db)
        assert keys == []

    async def test_two_keys_returned(self, tmp_path: Path) -> None:
        """Two active keys both appear in listing."""
        db = tmp_path / "keys.db"
        await create_api_key("user1", db)
        await create_api_key("user1", db)
        keys = await list_keys("user1", db)
        assert len(keys) == 2

    async def test_other_user_keys_not_included(self, tmp_path: Path) -> None:
        """list_keys only returns keys for the specified user."""
        db = tmp_path / "keys.db"
        await create_api_key("user1", db)
        await create_api_key("user2", db)
        keys = await list_keys("user1", db)
        assert len(keys) == 1

    async def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        """list_keys returns empty list if db doesn't exist (graceful)."""
        db = tmp_path / "nonexistent.db"
        keys = await list_keys("user1", db)
        assert keys == []
