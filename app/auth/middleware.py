"""OnGarde API key authentication middleware — E-006-S-004.

Provides ``authenticate_request()``: a FastAPI Depends()-compatible async
dependency that extracts and validates the OnGarde API key from incoming requests.

CRITICAL INVARIANT: authenticate_request() raises HTTP 401 BEFORE any scanning
occurs. The proxy handler MUST depend on this function so that auth failure
short-circuits the handler before scan_or_block() is called.

Header extraction precedence (architecture.md §7.4):
  1. X-OnGarde-Key: ong-<ulid>           (preferred — explicit OnGarde header)
  2. Authorization: Bearer ong-<ulid>    (fallback — if X-OnGarde-Key absent)

Non-ong- Authorization headers (e.g., 'Bearer sk-openai-...') are NOT consumed
here — they are forwarded unchanged to the upstream LLM provider.

Auth control:
  - ONGARDE_AUTH_REQUIRED=true  → key validation enforced (default — production mode)
  - ONGARDE_AUTH_REQUIRED=false → auth bypassed, user_id='anonymous' (testing/dev only)

The default is true. Set ONGARDE_AUTH_REQUIRED=false only for local development
or automated tests. Never run in production with auth disabled.

Architecture: architecture.md §7.4
Story: E-006-S-004
"""

from __future__ import annotations

import os
import re

from fastapi import HTTPException, Request

from app.auth.keys import validate_api_key
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Matches "Bearer ong-..." — extracts the ong- prefixed token.
# Does NOT match non-ong- bearer tokens (LLM API keys like sk-...).
_BEARER_ONG_RE = re.compile(r"^Bearer\s+(ong-\S+)", re.IGNORECASE)

def _is_auth_required() -> bool:
    """Read ONGARDE_AUTH_REQUIRED env var dynamically.

    Called per-request so that tests can override via monkeypatch.setenv()
    and the value reflects the current environment state.

    Returns:
        True if ONGARDE_AUTH_REQUIRED=true (case-insensitive), else False.
    """
    return os.environ.get("ONGARDE_AUTH_REQUIRED", "true").lower() == "true"


def _extract_ong_bearer(authorization: str) -> str | None:
    """Extract 'ong-...' token from 'Authorization: Bearer ong-...' header.

    Returns the full 'ong-...' token string if the Authorization header
    contains an ong- prefixed Bearer token.
    Returns None for all other Authorization values (including LLM API keys).

    Args:
        authorization: The raw Authorization header value (or empty string).

    Returns:
        str: The ong- prefixed token, or None if not present / not ong-.
    """
    if not authorization:
        return None
    m = _BEARER_ONG_RE.match(authorization.strip())
    return m.group(1) if m else None


async def authenticate_request(request: Request) -> str:
    """FastAPI dependency: authenticate the OnGarde API key.

    This dependency MUST run before any scan is initiated. FastAPI's dependency
    injection ensures the handler is short-circuited if this raises HTTPException.

    Header precedence:
      1. X-OnGarde-Key: ong-<ulid>    (preferred)
      2. Authorization: Bearer ong-... (fallback)

    Args:
        request: The FastAPI Request object (injected by dependency system).

    Returns:
        user_id (str): The authenticated user's ID on success.
                       Returns 'anonymous' when ONGARDE_AUTH_REQUIRED=false and
                       no key is present (dev/test bypass mode only).

    Raises:
        HTTPException(401): If no OnGarde key is present or the key is invalid
                            AND ONGARDE_AUTH_REQUIRED=true.

    Non-negotiable: HTTP 401 MUST be raised before scan_or_block() is called.
    Verified by AC-E006-08 test: scanner mock must not be called on 401 path.
    """
    # ── Extract key from headers ──────────────────────────────────────────
    key = request.headers.get("X-OnGarde-Key") or _extract_ong_bearer(
        request.headers.get("Authorization", "")
    )

    # ── Auth bypass mode (ONGARDE_AUTH_REQUIRED=false) ───────────────────
    # For local development and automated tests ONLY.
    # In bypass mode, ALL requests are treated as user_id='anonymous'.
    # NEVER set ONGARDE_AUTH_REQUIRED=false in production.
    if not _is_auth_required():
        return "anonymous"

    # ── Strict auth mode (ONGARDE_AUTH_REQUIRED=true — the default) ──────
    # All requests must carry a valid ong- API key.
    if not key or not key.startswith("ong-"):
        logger.warning(
            "Authentication failed: no OnGarde key",
            path=str(request.url.path),
            method=request.method,
        )
        raise HTTPException(
            status_code=401,
            detail="Missing OnGarde API key",
        )

    # ── Validate key (LRU-cached after first validation) ─────────────────
    user_id = await validate_api_key(key)

    if user_id is None:
        logger.warning(
            "Authentication failed: invalid key",
            path=str(request.url.path),
            method=request.method,
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key",
        )

    return user_id
