"""Audit backend factory — backend selection and initialization.

Backend selection logic (architecture.md §4.3):
  1. If SUPABASE_URL and SUPABASE_KEY are both set: use SupabaseBackend
  2. Otherwise: use LocalSQLiteBackend (default)

LocalSQLiteBackend path:
  Default: ~/.ongarde/audit.db
  Override: ONGARDE_AUDIT_DB_PATH environment variable

PRAGMA version guard:
  LocalSQLiteBackend.initialize() raises RuntimeError if PRAGMA user_version
  is not 0 (fresh) or 1 (expected). The FastAPI lifespan propagates this
  RuntimeError to refuse startup — protecting against accidental schema migration.

Runtime: PRAGMA version guard check is performed inside initialize().
"""

from __future__ import annotations

import os

from app.audit.protocol import AuditBackend
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ─── Environment variable names ───────────────────────────────────────────────

_ENV_SUPABASE_URL = "SUPABASE_URL"
_ENV_SUPABASE_KEY = "SUPABASE_KEY"
_ENV_AUDIT_DB_PATH = "ONGARDE_AUDIT_DB_PATH"
_DEFAULT_AUDIT_DB_PATH = "~/.ongarde/audit.db"


async def create_audit_backend(config: object) -> AuditBackend:
    """Create and initialize the appropriate audit backend.

    Backend selection (architecture.md §4.3):
      - SUPABASE_URL + SUPABASE_KEY both set → SupabaseBackend
      - Otherwise                             → LocalSQLiteBackend (default)

    LocalSQLiteBackend path:
      - ONGARDE_AUDIT_DB_PATH env var (if set)
      - ~/.ongarde/audit.db (default)

    Raises:
      RuntimeError: If LocalSQLiteBackend.initialize() finds PRAGMA user_version
                    incompatible with the expected schema (version != 0 or 1).
                    Propagated to FastAPI lifespan → process exits non-zero.

    Args:
        config: Application Config object (unused in current implementation;
                retained for interface compatibility with E-001 stub).

    Returns:
        Initialized AuditBackend instance ready for use.
    """
    supabase_url = os.getenv(_ENV_SUPABASE_URL)
    supabase_key = os.getenv(_ENV_SUPABASE_KEY)

    if supabase_url and supabase_key:
        return await _create_supabase_backend(supabase_url, supabase_key)
    else:
        return await _create_local_sqlite_backend()


async def _create_supabase_backend(url: str, key: str) -> AuditBackend:
    """Create and initialize a SupabaseBackend.

    All initialization failures are swallowed — SupabaseBackend returns
    a no-op-like backend if the client fails to connect.
    """
    from app.audit.supabase_backend import SupabaseBackend

    backend = SupabaseBackend(url=url, key=key)
    await backend.initialize()
    logger.info(
        "audit_backend_selected",
        backend="SupabaseBackend",
        # Never log the key — log only the URL host portion
        supabase_host=url.split("//")[-1].split(".")[0] if "//" in url else "unknown",
    )
    return backend


async def _create_local_sqlite_backend() -> AuditBackend:
    """Create and initialize a LocalSQLiteBackend.

    Path preference:
      1. ONGARDE_AUDIT_DB_PATH environment variable
      2. ~/.ongarde/audit.db (default)

    Raises:
      RuntimeError: If PRAGMA user_version indicates an incompatible schema.
                    Propagated to FastAPI lifespan → startup refused.
    """
    from app.audit.sqlite_backend import LocalSQLiteBackend

    db_path = os.getenv(_ENV_AUDIT_DB_PATH, _DEFAULT_AUDIT_DB_PATH)
    backend = LocalSQLiteBackend(db_path=db_path)

    # initialize() raises RuntimeError if user_version is incompatible.
    # This propagates to FastAPI lifespan → process exits non-zero.
    # This is the PRAGMA version guard (architecture.md §4.3).
    await backend.initialize()

    logger.info(
        "audit_backend_selected",
        backend="LocalSQLiteBackend",
        db_path=db_path,
    )
    return backend
