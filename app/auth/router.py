"""Dashboard API endpoints for key management — E-006-S-005.

Provides:
  GET  /dashboard/api/keys          — list active keys (masked)
  POST /dashboard/api/keys/rotate   — rotate a key by ID (new plaintext shown once)
  POST /dashboard/api/keys/revoke   — revoke a key by ID

All endpoints require authentication via Depends(authenticate_request).
Plaintext keys are NEVER returned except in the rotation response (once only).

Architecture: architecture.md §7.1, §7.4
Story: E-006-S-005
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.keys import (
    InvalidKeyError,
    KeyLimitExceededError,
    create_api_key,
    list_keys,
    revoke_api_key,
    rotate_api_key_by_id,
)
from app.auth.limiter import KEY_MANAGEMENT_RATE_LIMIT, limiter
from app.auth.middleware import authenticate_request
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["api-keys"])


# ─── Request Models ───────────────────────────────────────────────────────────


class CreateKeyRequest(BaseModel):
    """Request body for POST /dashboard/api/keys.

    Intentionally empty — user_id is always sourced from the authenticated
    session (Depends(authenticate_request)), never from the request body.
    Accepting user_id in the body would allow privilege escalation (any caller
    creating keys attributed to arbitrary users). SEC-005.
    """


class RotateKeyRequest(BaseModel):
    """Request body for POST /dashboard/api/keys/rotate."""

    key_id: str
    """8-character lookup ID of the key to rotate (the 'id' column value)."""


class RevokeKeyRequest(BaseModel):
    """Request body for POST /dashboard/api/keys/revoke."""

    key_id: str
    """8-character lookup ID of the key to revoke."""


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/keys")
@limiter.limit(KEY_MANAGEMENT_RATE_LIMIT)
async def get_keys(
    request: Request,
    user_id: str = Depends(authenticate_request),
) -> dict:
    """List all active API keys for the authenticated user (masked representation).

    Returns:
        JSON: {"keys": [{id, masked_key, created_at, last_used_at}, ...]}

    The masked_key format: 'ong-...XXXX' where XXXX is the last 4 chars of
    the key's 8-char ID. The full plaintext key is NEVER returned.

    AC-E006-03: masked_key field only — no plaintext after initial creation.
    """
    keys = await list_keys(user_id=user_id)
    return {"keys": keys}


@router.post("/keys")
@limiter.limit(KEY_MANAGEMENT_RATE_LIMIT)
async def create_key(
    body: CreateKeyRequest,
    request: Request,
    user_id: str = Depends(authenticate_request),
) -> dict:
    """Create a new API key. Returns the plaintext key ONCE in the response.

    Used by the CLI init wizard (npx @ongarde/openclaw init) to bootstrap
    the first API key. The plaintext key is returned once; subsequent
    GET /keys calls return only the masked form.

    When ONGARDE_AUTH_REQUIRED=false (dev/test bypass mode), user_id='anonymous'
    and no existing key is required. This allows the CLI init wizard to create
    the first key without a chicken-and-egg problem.
    In production (ONGARDE_AUTH_REQUIRED=true), the init wizard calls this
    endpoint once during setup before a key exists — the first call is allowed
    when the key store is empty (zero active keys for any user).

    Returns:
        JSON: {key, masked_key, id, message}
        - key:        full plaintext 'ong-...' key (show ONCE — never again)
        - masked_key: 'ong-...XXXX' for display after this response
        - id:         8-char ULID lookup ID

    Raises:
        HTTP 400: Maximum keys already reached (2 per user).

    Story: E-007-S-001
    """
    # Always use the authenticated user_id — never accept user_id from the body
    # (SEC-005: prevents privilege escalation / key attribution spoofing).
    effective_user_id = user_id

    try:
        plaintext_key, _key_hash = await create_api_key(user_id=effective_user_id)
    except KeyLimitExceededError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Key ID is the raw ULID portion (after 'ong-' prefix)
    # This is the value stored as PRIMARY KEY in api_keys table
    key_id = plaintext_key[4:]  # full ULID — unique PK

    # Masked form: 'ong-...XXXX' where XXXX = last 4 of the ULID
    masked = f"ong-...{key_id[-4:]}"

    logger.info("API key created via init wizard", user_id=effective_user_id)

    return {
        "key": plaintext_key,
        "masked_key": masked,
        "id": key_id,
        "message": "API key created. Store this key — it will not be shown again.",
    }


@router.post("/keys/rotate")
@limiter.limit(KEY_MANAGEMENT_RATE_LIMIT)
async def rotate_key(
    body: RotateKeyRequest,
    request: Request,
    user_id: str = Depends(authenticate_request),
) -> dict:
    """Rotate an API key. Returns the new plaintext key ONCE in the response.

    The new key is shown exactly once. A subsequent GET /keys returns only
    the masked form. The old key is invalidated immediately (LRU cache cleared).

    Args:
        body.key_id: 8-char ID of the key to rotate (from GET /keys response).

    Returns:
        JSON: {new_key, masked_key, message}
        - new_key: full plaintext new key (show to user ONCE — never again)
        - masked_key: masked form for display after this response

    Raises:
        HTTP 400: Key not found or doesn't belong to authenticated user.

    AC-E006-04: new plaintext key in rotation response only.
    AC-E006-06: KEY_ROTATED audit event logged.
    """
    # Retrieve audit backend from app state if available (fire-and-forget logging)
    audit_backend = getattr(request.app.state, "audit_backend", None)

    try:
        new_plaintext, _ = await rotate_api_key_by_id(
            user_id=user_id,
            key_id=body.key_id,
            audit_backend=audit_backend,
        )
    except InvalidKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyLimitExceededError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The last 4 chars of the new ULID for masking:
    # new_plaintext = "ong-<26-char-ULID>"
    new_ulid = new_plaintext[4:]  # strip "ong-" prefix
    masked = f"ong-...{new_ulid[-4:]}"

    logger.info("Key rotated via dashboard", user_id=user_id, old_key_id=body.key_id)

    return {
        "new_key": new_plaintext,
        "masked_key": masked,
        "message": "Key rotated successfully. Store this key — it will not be shown again.",
    }


@router.post("/keys/revoke")
@limiter.limit(KEY_MANAGEMENT_RATE_LIMIT)
async def revoke_key(
    body: RevokeKeyRequest,
    request: Request,
    user_id: str = Depends(authenticate_request),
) -> dict:
    """Revoke an API key. The key is immediately invalidated.

    Args:
        body.key_id: 8-char ID of the key to revoke (from GET /keys response).

    Returns:
        JSON: {message, revoked_id}

    Raises:
        HTTP 404: Key not found or doesn't belong to authenticated user.

    AC-E006-06: KEY_REVOKED audit event logged.
    """
    audit_backend = getattr(request.app.state, "audit_backend", None)

    revoked = await revoke_api_key(
        user_id=user_id,
        key_id=body.key_id,
        audit_backend=audit_backend,
    )

    if not revoked:
        raise HTTPException(
            status_code=404,
            detail=f"Key '{body.key_id}' not found or already revoked.",
        )

    logger.info("Key revoked via dashboard", user_id=user_id, key_id=body.key_id)

    return {
        "message": "Key revoked successfully.",
        "revoked_id": body.key_id,
    }
