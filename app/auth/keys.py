"""OnGarde API key CRUD operations — E-006.

Implements:
  - init_key_store()         — create SQLite schema, chmod 0600 (E-006-S-001)
  - create_api_key()         — generate ong-<ULID>, bcrypt rounds=12 (E-006-S-001)
  - validate_api_key()       — prefix lookup + bcrypt + LRU cache (E-006-S-002)
  - clear_key_cache()        — invalidate LRU cache (E-006-S-002)
  - rotate_api_key()         — atomic key rotation (E-006-S-003)
  - rotate_api_key_by_id()   — rotation by 8-char ID, no old plaintext needed (E-006-S-005)
  - revoke_api_key()         — mark active=0 (E-006-S-003)
  - list_keys()              — masked key list for dashboard (E-006-S-005)

Non-negotiables (enforced unconditionally):
  - Plaintext NEVER stored in keys.db — only bcrypt hash
  - os.chmod(db_path, 0o600) on every init call
  - bcrypt rounds MUST be exactly 12
  - aiosqlite ONLY — no sqlite3 synchronous calls
  - clear_key_cache() MUST be called synchronously (not fire-and-forget)
    before rotate/revoke returns

Architecture: architecture.md §7.1–§7.4
Stories: E-006-S-001, E-006-S-002, E-006-S-003, E-006-S-005
"""

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
import bcrypt

from app.utils.logger import get_logger
from app.utils.ulid import generate_ulid

logger = get_logger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

#: Default location of the key store. Override with ONGARDE_KEYS_DB_PATH env var.
_DEFAULT_KEYS_DB_PATH: str = str(Path.home() / ".ongarde" / "keys.db")

#: bcrypt cost factor — non-negotiable; must be 12 (architecture.md §7.2)
_BCRYPT_ROUNDS: int = 12

#: Maximum active keys per user (architecture.md §7.3)
_MAX_KEYS_PER_USER: int = 2

#: LRU cache max size for key validation results
_CACHE_MAXSIZE: int = 1000


# ─── Exceptions ───────────────────────────────────────────────────────────────


class KeyLimitExceededError(Exception):
    """Raised when user attempts to create more than _MAX_KEYS_PER_USER active keys.

    HTTP mapping: 400 Bad Request with code='key_limit_exceeded'
    """

    code: str = "key_limit_exceeded"

    def __init__(
        self,
        message: str = (
            "Maximum active keys (2) reached. "
            "Revoke an existing key before creating a new one."
        ),
    ) -> None:
        super().__init__(message)
        self.message = message


class InvalidKeyError(Exception):
    """Raised when an operation references a key that is invalid, absent, or revoked.

    HTTP mapping: 400 Bad Request (rotate) or 404 Not Found (revoke)
    """

    def __init__(self, message: str = "Invalid or revoked API key") -> None:
        super().__init__(message)
        self.message = message


# ─── LRU Cache (module-level) ─────────────────────────────────────────────────
# We use an OrderedDict-based manual LRU because:
#   1. functools.lru_cache cannot wrap async functions.
#   2. We need explicit cache_clear() from rotation/revocation paths.
#   3. OrderedDict.move_to_end() provides O(1) LRU operations.
#
# Cache entry: key (full plaintext "ong-...") -> user_id (str)
#
# IMPORTANT: This cache must be cleared synchronously (not via create_task)
# on rotation and revocation. The invariant is that after clear_key_cache()
# returns, NO cached validation for any key is present.

_cache: OrderedDict[str, str] = OrderedDict()


def _cache_get(key: str) -> Optional[str]:
    """Return cached user_id for key, or None on cache miss."""
    if key in _cache:
        _cache.move_to_end(key)  # mark as recently used
        return _cache[key]
    return None


def _cache_set(key: str, user_id: str) -> None:
    """Cache a validated (key → user_id) mapping, evicting LRU entry if full."""
    if key in _cache:
        _cache.move_to_end(key)
        _cache[key] = user_id
        return
    if len(_cache) >= _CACHE_MAXSIZE:
        _cache.popitem(last=False)  # evict the least-recently-used entry
    _cache[key] = user_id


def clear_key_cache() -> None:
    """Invalidate ALL cached key validations.

    Must be called synchronously (not via create_task) before rotate/revoke returns.
    After this returns, the next validate_api_key() call for ANY key hits the database.
    """
    _cache.clear()
    logger.debug("Key validation cache cleared")


# ─── Path Resolution ──────────────────────────────────────────────────────────


def _resolve_db_path(db_path: Optional[Path]) -> Path:
    """Resolve the keys.db path from the argument or environment variable."""
    if db_path is not None:
        return db_path
    env_path = os.environ.get("ONGARDE_KEYS_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path(_DEFAULT_KEYS_DB_PATH)


# ─── Schema ───────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_keys (
    id              TEXT PRIMARY KEY,
    key_hash        TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    last_used_at    TEXT,
    active          INTEGER NOT NULL DEFAULT 1
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_keys_user_active ON api_keys (user_id, active);
"""


# ─── E-006-S-001: Database Initialization ────────────────────────────────────


async def init_key_store(db_path: Optional[Path] = None) -> Path:
    """Initialize the SQLite key store with schema, permissions, and version.

    Idempotent — safe to call multiple times (CREATE TABLE IF NOT EXISTS).
    Sets file permissions to 0o600 (owner read/write only) unconditionally.

    Args:
        db_path: Path to keys.db. If None, uses ONGARDE_KEYS_DB_PATH env var
                 or default ~/.ongarde/keys.db.

    Returns:
        Resolved Path to the key store file.

    Raises:
        OSError: If the parent directory cannot be created or chmod fails.

    Non-negotiables:
        - PRAGMA user_version = 1 set on creation.
        - os.chmod(path, 0o600) called on every invocation.
        - aiosqlite only — no sqlite3.
    """
    path = _resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(path)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(_CREATE_TABLE_SQL)
        await db.execute(_CREATE_INDEX_SQL)
        await db.execute("PRAGMA user_version = 1")
        await db.commit()

    # Set permissions on every init call — idempotent and safe.
    # This ensures the file is always 0600 regardless of umask at creation time.
    os.chmod(path, 0o600)

    logger.debug("Key store initialized", path=str(path))
    return path


# ─── E-006-S-001: Key Creation ───────────────────────────────────────────────


async def create_api_key(
    user_id: str,
    db_path: Optional[Path] = None,
) -> tuple[str, str]:
    """Generate a new ong-<ULID> API key, hash it, and store the hash.

    NEVER stores the plaintext key. The caller receives the plaintext exactly
    once — it must be shown to the user and never retrieved again.

    Key format: ong-<26-char-ULID> (30 chars total)
    bcrypt cost: rounds=12 (non-negotiable per architecture.md §7.2)

    Args:
        user_id:  The user this key belongs to.
        db_path:  Path to keys.db. If None, uses default resolution.

    Returns:
        (plaintext_key, key_hash) — store key_hash; show plaintext_key ONCE.

    Raises:
        KeyLimitExceededError: If user already has _MAX_KEYS_PER_USER active keys.

    Non-negotiables:
        - Plaintext key NEVER written to database.
        - bcrypt rounds MUST be exactly 12.
        - init_key_store() called first (idempotent).
    """
    path = _resolve_db_path(db_path)
    await init_key_store(path)

    async with aiosqlite.connect(str(path)) as db:
        # ── 2-key limit check ──────────────────────────────────────────────
        async with db.execute(
            "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND active = 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        count = row[0] if row else 0
        if count >= _MAX_KEYS_PER_USER:
            raise KeyLimitExceededError()

        # ── Generate key ──────────────────────────────────────────────────
        raw_ulid = generate_ulid()          # 26-char uppercase ULID
        key_id = raw_ulid                   # full ULID = PRIMARY KEY (guaranteed unique)
        plaintext = f"ong-{raw_ulid}"       # full key shown once

        # ── bcrypt hash (rounds=12) ───────────────────────────────────────
        # This is intentionally slow (~80ms) — per-creation cost only.
        salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        key_hash = bcrypt.hashpw(plaintext.encode(), salt).decode()

        # ── Insert into DB (hash only, never plaintext) ───────────────────
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO api_keys (id, key_hash, user_id, created_at, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (key_id, key_hash, user_id, now),
        )
        await db.commit()

    logger.info("API key created", user_id=user_id, key_id=key_id)
    return plaintext, key_hash


# ─── E-006-S-002: Key Validation + LRU Cache ─────────────────────────────────


async def validate_api_key(
    key: str,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Validate an OnGarde API key. Returns user_id on success, None on failure.

    Validation flow:
      1. Format check: must start with 'ong-' and be long enough.
      2. LRU cache lookup: cache hit → return immediately (< 0.1ms).
      3. DB lookup by key_id (first 8 chars of ULID after 'ong-').
      4. Check active=1: inactive key → return None immediately.
      5. bcrypt.checkpw(): slow (~80ms) only on cache miss.
      6. On success: cache result and return user_id.

    Args:
        key:      Full plaintext key (e.g., 'ong-01HZXXXXXXXXXXXXXXXXXXXXXXX').
        db_path:  Path to keys.db. If None, uses default resolution.

    Returns:
        user_id (str) if key is valid and active, else None.

    Non-negotiables:
        - bcrypt.checkpw() ONLY called when prefix lookup finds an active row.
        - Cache key is the full plaintext key string.
        - LRU cache maxsize=1000; evicts LRU when full.
    """
    if not key or not key.startswith("ong-") or len(key) < 12:
        return None

    # ── 1. LRU cache check ────────────────────────────────────────────────
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # ── 2. Full-ULID DB lookup ────────────────────────────────────────────
    # key = "ong-" + raw_ulid (26 chars); id = full raw_ulid (unique PRIMARY KEY)
    key_id = key[4:]  # skip "ong-" prefix — full ULID portion

    path = _resolve_db_path(db_path)
    try:
        async with aiosqlite.connect(str(path)) as db:
            async with db.execute(
                "SELECT key_hash, user_id FROM api_keys WHERE id = ? AND active = 1",
                (key_id,),
            ) as cursor:
                row = await cursor.fetchone()
    except Exception as exc:
        logger.warning("Key validation DB error", error=str(exc))
        return None

    if row is None:
        return None

    key_hash, user_id = row

    # ── 3. bcrypt verify (slow path — cache miss only) ────────────────────
    try:
        if not bcrypt.checkpw(key.encode(), key_hash.encode()):
            return None
    except Exception as exc:
        logger.warning("bcrypt verify error", error=str(exc))
        return None

    # ── 4. Cache result ───────────────────────────────────────────────────
    _cache_set(key, user_id)

    # ── 5. Update last_used_at (fire-and-forget — non-blocking) ──────────
    asyncio.create_task(_update_last_used(key_id, path))

    return user_id


async def _update_last_used(key_id: str, path: Path) -> None:
    """Update last_used_at timestamp for a key. Fire-and-forget."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(path)) as db:
            await db.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (now, key_id),
            )
            await db.commit()
    except Exception as exc:
        logger.debug("Failed to update last_used_at", key_id=key_id, error=str(exc))


# ─── E-006-S-003: Key Rotation ───────────────────────────────────────────────


async def rotate_api_key(
    user_id: str,
    old_key: str,
    db_path: Optional[Path] = None,
    audit_backend=None,
) -> tuple[str, str]:
    """Rotate an API key by providing the old plaintext key.

    Validates old_key is active for user_id. Generates a new key. Atomically
    inserts new key + deactivates old key. Clears LRU cache.

    Args:
        user_id:       User who owns the key.
        old_key:       Full plaintext old key being rotated.
        db_path:       Path to keys.db (default resolution if None).
        audit_backend: AuditBackend instance for event logging (optional).

    Returns:
        (new_plaintext_key, new_key_hash) — show new_plaintext_key ONCE.

    Raises:
        InvalidKeyError: If old_key is not active for user_id.

    Non-negotiables:
        - clear_key_cache() called synchronously BEFORE function returns.
        - Plaintext new key NEVER stored in DB.
        - bcrypt rounds=12.
    """
    if not old_key or not old_key.startswith("ong-") or len(old_key) < 30:
        raise InvalidKeyError("Invalid key format")

    old_key_id = old_key[4:]  # full ULID portion
    path = _resolve_db_path(db_path)

    async with aiosqlite.connect(str(path)) as db:
        # Validate old key belongs to this user
        async with db.execute(
            "SELECT id FROM api_keys WHERE id = ? AND user_id = ? AND active = 1",
            (old_key_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise InvalidKeyError("Key not found or already revoked")

        # Generate new key
        raw_ulid = generate_ulid()
        new_key_id = raw_ulid  # full ULID = PRIMARY KEY
        new_plaintext = f"ong-{raw_ulid}"

        salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        new_hash = bcrypt.hashpw(new_plaintext.encode(), salt).decode()

        now = datetime.now(timezone.utc).isoformat()

        # Atomic swap: INSERT new + deactivate old
        await db.execute(
            "INSERT INTO api_keys (id, key_hash, user_id, created_at, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (new_key_id, new_hash, user_id, now),
        )
        await db.execute(
            "UPDATE api_keys SET active = 0 WHERE id = ? AND user_id = ?",
            (old_key_id, user_id),
        )
        await db.commit()

    # Clear LRU cache synchronously — must happen before return
    clear_key_cache()

    logger.info(
        "API key rotated",
        user_id=user_id,
        old_key_id=old_key_id[:8],  # log only prefix for brevity
        new_key_id=new_key_id[:8],
    )

    # Fire-and-forget audit event
    if audit_backend is not None:
        _log_key_event(audit_backend, user_id, "KEY_ROTATED")

    return new_plaintext, new_hash


async def rotate_api_key_by_id(
    user_id: str,
    key_id: str,
    db_path: Optional[Path] = None,
    audit_backend=None,
) -> tuple[str, str]:
    """Rotate a key by its 8-char ID. No old plaintext required.

    Used by the dashboard (E-006-S-005) where the full plaintext key is not
    available — only the key_id from the GET /keys listing.

    Args:
        user_id:  User who owns the key.
        key_id:   8-char lookup ID (the 'id' column value).
        db_path:  Path to keys.db.
        audit_backend: For KEY_ROTATED event logging.

    Returns:
        (new_plaintext_key, new_key_hash).

    Raises:
        InvalidKeyError: If key_id not found or doesn't belong to user_id.
    """
    path = _resolve_db_path(db_path)

    async with aiosqlite.connect(str(path)) as db:
        async with db.execute(
            "SELECT id FROM api_keys WHERE id = ? AND user_id = ? AND active = 1",
            (key_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise InvalidKeyError("Key not found or already revoked")

        raw_ulid = generate_ulid()
        new_key_id = raw_ulid  # full ULID = PRIMARY KEY
        new_plaintext = f"ong-{raw_ulid}"

        salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        new_hash = bcrypt.hashpw(new_plaintext.encode(), salt).decode()

        now = datetime.now(timezone.utc).isoformat()

        await db.execute(
            "INSERT INTO api_keys (id, key_hash, user_id, created_at, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (new_key_id, new_hash, user_id, now),
        )
        await db.execute(
            "UPDATE api_keys SET active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        await db.commit()

    clear_key_cache()

    logger.info(
        "API key rotated by ID",
        user_id=user_id,
        old_key_id=key_id[:8] if len(key_id) >= 8 else key_id,
        new_key_id=new_key_id[:8],
    )

    if audit_backend is not None:
        _log_key_event(audit_backend, user_id, "KEY_ROTATED")

    return new_plaintext, new_hash


# ─── E-006-S-003: Key Revocation ─────────────────────────────────────────────


async def revoke_api_key(
    user_id: str,
    key_id: str,
    db_path: Optional[Path] = None,
    audit_backend=None,
) -> bool:
    """Revoke an API key by its 8-char ID.

    Marks the key as active=0. Clears the LRU cache. Logs a KEY_REVOKED audit event.

    Args:
        user_id:  Owner of the key (security check — cannot revoke others' keys).
        key_id:   8-char ULID prefix (the 'id' column value).
        db_path:  Path to keys.db.
        audit_backend: For KEY_REVOKED event logging.

    Returns:
        True if a row was deactivated, False if no matching active key found.

    Non-negotiables:
        - clear_key_cache() called synchronously BEFORE function returns.
    """
    path = _resolve_db_path(db_path)

    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            "UPDATE api_keys SET active = 0 "
            "WHERE id = ? AND user_id = ? AND active = 1",
            (key_id, user_id),
        )
        await db.commit()
        rowcount = db.total_changes

    # Clear LRU cache synchronously — must happen before return
    clear_key_cache()

    if rowcount > 0:
        logger.info("API key revoked", user_id=user_id, key_id=key_id)
        if audit_backend is not None:
            _log_key_event(audit_backend, user_id, "KEY_REVOKED")
        return True

    logger.debug("revoke_api_key: no matching active key", user_id=user_id, key_id=key_id)
    return False


# ─── E-006-S-005: Key Listing (Masked) ───────────────────────────────────────


async def list_keys(
    user_id: str,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Return all active keys for a user as masked representations.

    The masked_key format: 'ong-...XXXX' where XXXX is the last 4 chars of
    the key_id (8-char ULID prefix). Full plaintext key is NEVER returned.

    Args:
        user_id:  The user whose keys to list.
        db_path:  Path to keys.db.

    Returns:
        List of dicts: [{id, masked_key, created_at, last_used_at}, ...]
        Sorted by created_at DESC (newest first).
    """
    path = _resolve_db_path(db_path)

    try:
        async with aiosqlite.connect(str(path)) as db:
            async with db.execute(
                "SELECT id, created_at, last_used_at "
                "FROM api_keys WHERE user_id = ? AND active = 1 "
                "ORDER BY created_at DESC",
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()
    except Exception as exc:
        logger.warning("list_keys DB error", user_id=user_id, error=str(exc))
        return []

    return [
        {
            "id": row[0],
            "masked_key": f"ong-...{row[0][-4:]}",
            "created_at": row[1],
            "last_used_at": row[2],
        }
        for row in rows
    ]


# ─── Audit Event Helper ───────────────────────────────────────────────────────


def _log_key_event(audit_backend, user_id: str, event_type: str) -> None:
    """Fire-and-forget audit event for key lifecycle events.

    Uses asyncio.create_task() — never awaited, never blocks the caller.
    event_type is stored as rule_id (KEY_CREATED, KEY_ROTATED, KEY_REVOKED).
    """
    try:
        from app.audit.models import AuditEvent

        event = AuditEvent(
            scan_id=generate_ulid(),
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            action="ALLOW",       # key management events use ALLOW + rule_id
            direction="REQUEST",
            rule_id=event_type,   # KEY_CREATED / KEY_ROTATED / KEY_REVOKED
        )
        asyncio.create_task(audit_backend.log_event(event))
    except Exception as exc:
        logger.warning(
            "Failed to schedule key audit event",
            event_type=event_type,
            user_id=user_id,
            error=str(exc),
        )
