"""BLOCK and upstream-unavailable HTTP response builders — E-001-S-006.

Provides two factory functions that build the correctly-formed HTTP responses for
the two distinct failure modes in the OnGarde proxy:

  build_block_response():
      HTTP 400 — security policy BLOCK (scan gate returned Action.BLOCK).
      MUST include ``X-OnGarde-Block: true`` and ``X-OnGarde-Scan-ID: <ulid>``.
      Body: OpenAI-compatible error format with OnGarde extension fields.

  build_upstream_unavailable_response():
      HTTP 502 — upstream LLM provider unreachable (connectivity failure).
      MUST NOT include ``X-OnGarde-Block`` — a connectivity error is NOT a security block.
      Body: minimal error object identifying the upstream as unavailable.

These two response types are NEVER confused (AC-E001-06):
  - 502 has no X-OnGarde-Block header
  - 400 BLOCK always has X-OnGarde-Block: true

Architecture reference: architecture.md §5.2
Story: E-001-S-006 (AC-E001-06)
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from app.models.scan import ScanResult


def build_block_response(result: ScanResult) -> JSONResponse:
    """Build the HTTP 400 BLOCK response in OpenAI-compatible format.

    Called when ``scan_or_block()`` returns ``Action.BLOCK``.
    NEVER called for upstream connectivity errors (those use
    ``build_upstream_unavailable_response()``).

    The response body follows the OpenAI error schema extended with an
    ``ongarde`` field for block metadata:

    .. code-block:: json

        {
          "error": {
            "message": "Request blocked by OnGarde security policy",
            "type": "ongarde_block",
            "code": "policy_violation"
          },
          "ongarde": {
            "blocked": true,
            "rule_id": "<rule_id or null>",
            "risk_level": "<CRITICAL|HIGH|MEDIUM|LOW or null>",
            "scan_id": "<ulid>",
            "redacted_excerpt": "<sanitised excerpt or null>",
            "suppression_hint": null,
            "test": false
          }
        }

    Security invariants:
      - ``redacted_excerpt`` is always sanitised — raw credentials are NEVER present.
      - ``suppression_hint`` is None in this story (real generation in E-002/E-009).
      - No scan internals or config details are leaked in the body.

    Headers set:
      - ``X-OnGarde-Block: true`` — REQUIRED on ALL BLOCK responses (AC-E001-06).
      - ``X-OnGarde-Scan-ID: <ulid>`` — correlates with audit trail and logs.

    Args:
        result: ScanResult with action == Action.BLOCK.

    Returns:
        JSONResponse with status_code=400 and the required security headers.
    """
    response = JSONResponse(
        status_code=400,
        content={
            "error": {
                "message": "Request blocked by OnGarde security policy",
                "type": "ongarde_block",
                "code": "policy_violation",
            },
            "ongarde": {
                "blocked": True,
                "rule_id": result.rule_id,
                "risk_level": result.risk_level.value if result.risk_level else None,
                "scan_id": result.scan_id,
                "redacted_excerpt": result.redacted_excerpt,
                "suppression_hint": result.suppression_hint,
                "test": result.test,
            },
        },
    )
    # REQUIRED: all BLOCK responses carry these headers (AC-E001-06)
    response.headers["X-OnGarde-Block"] = "true"
    response.headers["X-OnGarde-Scan-ID"] = result.scan_id
    return response


def build_upstream_unavailable_response(
    scan_id: str,
    reason: str = "",
) -> JSONResponse:
    """Build the HTTP 502 response for upstream connectivity failures.

    Called when the upstream LLM provider is unreachable due to a network error
    (``httpx.ConnectError``, ``httpx.TimeoutException``, ``httpx.RemoteProtocolError``).

    CRITICAL: This response MUST NOT include ``X-OnGarde-Block`` — a missing upstream
    is NOT a security block; it is a gateway error. The two cases are never confused
    (AC-E001-06): 502 = connectivity failure, 400 = policy BLOCK.

    Headers set:
      - ``X-OnGarde-Scan-ID: <ulid>`` — correlates with audit trail.
      - ``X-OnGarde-Block`` is explicitly NOT set (verified by tests).

    Args:
        scan_id: ULID for this request, used for audit correlation.
        reason:  Short human-readable reason for the failure (e.g. ``"ConnectError"``).
                 Included in the response body for operator debugging.
                 MUST NOT contain credential data or internal config details.

    Returns:
        JSONResponse with status_code=502 and NO ``X-OnGarde-Block`` header.
    """
    response = JSONResponse(
        status_code=502,
        content={
            "error": {
                "message": "Upstream LLM provider unavailable",
                "code": "upstream_unavailable",
                "detail": reason if reason else None,
            }
        },
    )
    # Scan-ID is included for operator correlation — this is NOT a block header.
    # X-OnGarde-Block is intentionally absent — a connectivity error is NOT a BLOCK.
    response.headers["X-OnGarde-Scan-ID"] = scan_id
    return response
