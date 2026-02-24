"""OnGarde API key management package — E-006.

Public API:
  - create_api_key()         — generate ong-<ULID> key, bcrypt hash, store (E-006-S-001)
  - validate_api_key()       — prefix lookup + bcrypt verify + LRU cache (E-006-S-002)
  - rotate_api_key()         — generate new key, deactivate old, clear cache (E-006-S-003)
  - rotate_api_key_by_id()   — same as above but accepts 8-char key_id (E-006-S-005)
  - revoke_api_key()         — mark key inactive, clear cache (E-006-S-003)
  - list_keys()              — return masked active keys for user (E-006-S-005)
  - init_key_store()         — create schema, chmod 0600, idempotent (E-006-S-001)
  - authenticate_request()   — FastAPI Depends() dependency (E-006-S-004)
  - KeyLimitExceededError    — raised when 2-key maximum is exceeded (E-006-S-002)
  - InvalidKeyError          — raised when key not found / already revoked (E-006-S-003)
"""

from __future__ import annotations

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
from app.auth.middleware import authenticate_request

__all__ = [
    "KeyLimitExceededError",
    "InvalidKeyError",
    "create_api_key",
    "init_key_store",
    "list_keys",
    "revoke_api_key",
    "rotate_api_key",
    "rotate_api_key_by_id",
    "validate_api_key",
    "clear_key_cache",
    "authenticate_request",
]
