"""Async HTTP proxy handler for OnGarde — E-001-S-002 / E-001-S-003 / E-001-S-005 / E-001-S-006.

Implements transparent, byte-identical proxy forwarding for all four LLM endpoints:
  - /v1/chat/completions  (OpenAI)
  - /v1/completions       (OpenAI legacy)
  - /v1/embeddings        (OpenAI)
  - /v1/messages          (Anthropic)

Key design properties:
  - Byte-identity forwarding: request body forwarded as raw bytes; never parsed/re-encoded
  - Shared httpx.AsyncClient at app.state.http_client — never instantiated per-request
  - Upstream URL routing from app.state.config (openai / anthropic / custom)
  - Response buffer routing (E-001-S-005):
      ≤ 512 KB response → buffered via aread() for scanning (E-002/E-003)
      > 512 KB response → StreamingResponse via _stream_response_scan() stub (E-004)
      SSE (text/event-stream) → always streaming path
  - ≤ 5ms p99 proxy overhead at 100 concurrent requests (AC-E001-04)
  - No blocking I/O anywhere — async/await throughout

Header manipulation (E-001-S-003):
  - build_upstream_headers(): strips X-OnGarde-Key, Authorization: Bearer ong-*,
    hop-by-hop headers; injects X-OnGarde-Scan-ID ULID; forwards all others unchanged.
  - build_agent_response_headers(): strips hop-by-hop from upstream response;
    passes rate-limit headers and all others through unchanged.
  - HOP_BY_HOP_HEADERS is defined in headers.py and imported here (single source of truth).

Failure mode separation (E-001-S-006, AC-E001-06):
  - Action.BLOCK → HTTP 400 with X-OnGarde-Block: true; upstream NEVER called.
  - httpx.ConnectError / TimeoutException / RemoteProtocolError → HTTP 502 (NO X-OnGarde-Block).
  - Upstream HTTP 4xx/5xx → passed through as-is (NOT converted to 502).
  - httpx.InvalidURL → HTTP 500 (configuration error, logged at ERROR level).
  - 502 ≠ 400: these two cases are never confused.

Architecture reference: architecture.md §1.4, §5.1, §5.2, §9.3, §10.1
Stories: E-001-S-002, E-001-S-003, E-001-S-005, E-001-S-006
"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.middleware import authenticate_request
from app.config import Config
from app.constants import MAX_RESPONSE_BUFFER_BYTES
from app.models.block import build_block_response, build_upstream_unavailable_response
from app.models.scan import Action, RiskLevel, ScanResult
from app.proxy.headers import (
    build_agent_response_headers,
    build_upstream_headers,
)
from app.proxy.streaming import emit_stream_abort
from app.scanner.safe_scan import scan_or_block
from app.scanner.streaming_scanner import StreamingScanner
from app.utils.logger import get_logger
from app.utils.ulid import generate_ulid

if TYPE_CHECKING:
    from app.utils.health import StreamingMetricsTracker

logger = get_logger(__name__)

# ─── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(tags=["proxy"])

# ─── Constants ────────────────────────────────────────────────────────────────

# HOP_BY_HOP_HEADERS is now defined in app/proxy/headers.py and imported above.
# It is re-exported from this module for backward compatibility with any tests or
# code that previously imported it from here.
#
# Do NOT redefine it — headers.py is the single canonical source of truth.

# Pool size matches --limit-concurrency 100 (architecture.md §9.3).
# Ensures every concurrent upstream connection has a pooled slot available.
POOL_MAX_CONNECTIONS: int = 100
POOL_MAX_KEEPALIVE: int = 100
POOL_KEEPALIVE_EXPIRY: float = 30.0  # seconds
PROXY_TIMEOUT: float = 30.0  # total request timeout

# ─── httpx.AsyncClient factory ────────────────────────────────────────────────


def create_http_client() -> httpx.AsyncClient:
    """Create the shared httpx.AsyncClient with connection pooling configured.

    This client is created once at lifespan startup and stored in
    app.state.http_client. It is NEVER instantiated per-request.

    Pool size = 100 matches --limit-concurrency 100 (architecture.md §9.3):
    every concurrent upstream connection has a dedicated pool slot.

    Returns:
        Configured httpx.AsyncClient ready for use.
    """
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=POOL_MAX_CONNECTIONS,
            max_keepalive_connections=POOL_MAX_KEEPALIVE,
            keepalive_expiry=POOL_KEEPALIVE_EXPIRY,
        ),
        timeout=httpx.Timeout(PROXY_TIMEOUT),
        follow_redirects=False,  # pass 3xx through to the agent; do not resolve
    )


# ─── Upstream routing ─────────────────────────────────────────────────────────


def _route_upstream(path: str, config: Config) -> str:
    """Return the upstream base URL for a given request path.

    Args:
        path: Relative request path captured by the catch-all route.
              Format: "v1/chat/completions" (no leading slash — the catch-all
              /{path:path} captures the path after the root /).
        config: app.state.config — Config instance with .upstream UpstreamConfig.

    Returns:
        Upstream base URL (e.g. "https://api.openai.com") without trailing slash.

    Routing rules (architecture.md §2.1, AC-E001-02):
        /v1/messages    → config.upstream.anthropic  (Anthropic Messages API)
        all other /v1/* → config.upstream.openai     (OpenAI-compatible endpoints)

    Note: The path guard in proxy_handler() ensures only /v1/* paths ever reach
    _route_upstream(). Non-/v1 paths are rejected with 404 before routing.
    """
    # Normalize path for comparison (strip any accidental leading slash)
    normalized = path.lstrip("/")

    # Anthropic endpoint: /v1/messages (and any sub-paths)
    if normalized == "v1/messages" or normalized.startswith("v1/messages/"):
        return config.upstream.anthropic

    # Default: OpenAI-compatible upstream
    return config.upstream.openai


# ─── Proxy handler ────────────────────────────────────────────────────────────


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_handler(
    request: Request,
    path: str,
    user_id: str = Depends(authenticate_request),
) -> Response:
    """Transparent HTTP proxy handler — all LLM endpoints.

    Intercepts and transparently forwards all /v1/* requests to the configured
    upstream LLM provider. This is the central interception point for:

      - Scan gate (stub here; real scanner in E-001-S-006 / E-002 / E-003)
      - Header manipulation (E-001-S-003): X-OnGarde-Key strip, scan-ID inject,
        rate-limit header passthrough, hop-by-hop stripping
      - Failure mode handling: 502 vs 400 (E-001-S-006)
      - Body size enforcement (E-001-S-005: 1 MB hard cap → 413)
      - Streaming response scanner (E-004)

    Readiness gate is enforced as a router-level FastAPI dependency (`require_ready`)
    registered in create_app() — no inline check needed here.

    AC-E001-01: Body byte-identity — request body forwarded as raw bytes unchanged.
    AC-E001-02: All four endpoints proxied.
    AC-E001-03: Response status + body passed through unchanged.
               Rate-limit headers forwarded with exact values from upstream.
    AC-E001-04: ≤ 5ms p99 overhead at 100 concurrent (shared client, no blocking I/O).

    Args:
        request: Incoming FastAPI request.
        path: URL path captured by catch-all (e.g. "v1/chat/completions").

    Returns:
        StreamingResponse with upstream status + bytes.

    Raises:
        Response 502: Upstream unreachable (ConnectError).
        Response 500: Unexpected proxy error.
    """
    # ── Path guard: only /v1/* paths are proxied ─────────────────────────────
    # M-002 (from E-001-S-002 review): the catch-all /{path:path} would otherwise
    # forward any unmatched path (e.g. /metrics, /v2/something) to the upstream LLM.
    # Only /v1/* paths are valid proxy targets; all others return 404.
    # Note: /health and / are registered on separate routers and never reach here.
    normalized_path = path.lstrip("/")
    if not normalized_path.startswith("v1/") and normalized_path != "v1":
        raise HTTPException(
            status_code=404,
            detail={"error": f"Not found: /{path}"},
        )

    # ── Generate scan ID at entry — used throughout for tracing ──────────────
    # AC-E001-01 (partial): X-OnGarde-Scan-ID injected on every forwarded request.
    # Same ULID used in structured log entry and (later) audit event scan_id field.
    scan_id: str = generate_ulid()

    # ── Get shared resources from app state ───────────────────────────────────
    config: Config = request.app.state.config
    http_client: httpx.AsyncClient = request.app.state.http_client

    # ── Determine upstream URL ────────────────────────────────────────────────
    upstream_base: str = _route_upstream(path, config)
    upstream_url: str = f"{upstream_base}/{path}"

    # ── Read request body as raw bytes (byte-identity requirement) ────────────
    # AC-E001-01: body must reach the upstream bit-for-bit identical.
    # We read once and reuse; FastAPI caches request.body() automatically.
    body: bytes = await request.body()

    # ── Scan gate (E-001-S-006) ───────────────────────────────────────────────
    # Called AFTER body size check (enforced by BodySizeLimitMiddleware — E-001-S-005)
    # and BEFORE upstream connection establishment.
    #
    # scan_or_block() is the ONLY entry point for all scan logic.
    # Stub: always returns Action.ALLOW (real implementation in E-002/E-003).
    # Invariant: NEVER raises, NEVER returns None — fail-safe (architecture.md §5.1).
    #
    # AC-E001-06: Action.BLOCK → HTTP 400 with X-OnGarde-Block: true.
    # The upstream connection is NEVER opened for a blocked request.
    content_text: str = body.decode("utf-8", errors="replace")
    scan_result = await scan_or_block(
        content=content_text,
        scan_pool=getattr(request.app.state, "scan_pool", None),
        scan_id=scan_id,
        audit_context={"scan_id": scan_id, "path": path, "method": request.method},
        latency_tracker=getattr(request.app.state, "latency_tracker", None),
        allowlist_loader=getattr(request.app.state, "allowlist_loader", None),  # E-009
    )

    # ── Audit logging for request scan result ─────────────────────────────────
    # Fire-and-forget via create_task — never blocks the request path.
    # E-009-S-004: ALLOW_SUPPRESSED is logged (never silently dropped).
    _audit_request_event(
        backend=getattr(request.app.state, "audit_backend", None),
        scan_result=scan_result,
        scan_id=scan_id,
        user_id=user_id,
        path=path,
    )

    if scan_result.action == Action.BLOCK:
        # Security BLOCK — HTTP 400 with X-OnGarde-Block: true.
        # Do NOT open upstream connection (AC-E001-06).
        logger.info(
            "request_blocked",
            scan_id=scan_id,
            rule_id=scan_result.rule_id,
            risk_level=str(scan_result.risk_level) if scan_result.risk_level else None,
            path=path,
        )
        return build_block_response(scan_result)

    if scan_result.action == Action.ALLOW_SUPPRESSED:
        # E-009: Allowlist suppression — forward to upstream but audit ALLOW_SUPPRESSED.
        logger.info(
            "request_suppressed",
            scan_id=scan_id,
            rule_id=scan_result.rule_id,
            allowlist_rule_id=scan_result.allowlist_rule_id,
            path=path,
        )

    # ── Build upstream request headers ────────────────────────────────────────
    # E-001-S-003: strip X-OnGarde-Key, strip Authorization: Bearer ong-*,
    # strip hop-by-hop headers, inject X-OnGarde-Scan-ID with the scan ULID.
    # All other headers (content-type, authorization with sk-*, anthropic-*, etc.)
    # are forwarded unchanged.
    upstream_headers: dict[str, str] = build_upstream_headers(
        request.headers.items(), scan_id
    )

    # ── Build and send upstream request ──────────────────────────────────────
    upstream_request = http_client.build_request(
        method=request.method,
        url=upstream_url,
        headers=upstream_headers,
        content=body,          # raw bytes — byte-identity guarantee (AC-E001-01)
        params=dict(request.query_params),  # query parameters pass through unchanged
    )

    try:
        upstream_response = await http_client.send(upstream_request, stream=True)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        # ── Upstream unreachable → HTTP 502 (AC-E001-06) ─────────────────────
        # ConnectError  : connection refused, host unreachable, DNS failure
        # TimeoutException: upstream response timeout (upstream too slow / gone)
        # RemoteProtocolError: upstream sent invalid HTTP
        #
        # CRITICAL: X-OnGarde-Block MUST NOT be set on this response.
        # A connectivity failure is NOT a security block — these two are never confused.
        logger.warning(
            "upstream_unavailable",
            scan_id=scan_id,
            upstream_url=upstream_url,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return build_upstream_unavailable_response(
            scan_id=scan_id,
            reason=type(exc).__name__,
        )
    except httpx.InvalidURL as exc:
        # ── Bad upstream URL → HTTP 500 (config error) ────────────────────────
        # httpx.InvalidURL is raised when the upstream URL is malformed (config error).
        # This is NOT a connectivity failure and NOT a security block — it is a
        # misconfiguration that should be fixed by the operator.
        logger.error(
            "invalid_upstream_url",
            scan_id=scan_id,
            upstream_url=upstream_url,
            error=str(exc),
        )
        return Response(
            status_code=500,
            content=b'{"error": {"message": "Internal configuration error", "code": "config_error"}}',
            media_type="application/json",
        )
    # NOTE: Upstream HTTP 4xx/5xx responses (e.g. 429, 401, 500) are NOT caught here.
    # They are forwarded to the agent as-is (AC-E001-06, taxonomy item 4).
    # Only connectivity-level httpx exceptions map to 502.

    # ── Build agent response headers ──────────────────────────────────────────
    # E-001-S-003: strip hop-by-hop headers; forward all others (including
    # rate-limit headers: x-ratelimit-*, retry-after) unchanged.
    agent_headers: dict[str, str] = build_agent_response_headers(
        upstream_response.headers
    )

    # ── Structured log entry — scan_id correlates with audit events ───────────
    logger.info(
        "request_proxied",
        scan_id=scan_id,
        method=request.method,
        path=path,
        upstream=upstream_url,
        status_code=upstream_response.status_code,
    )

    # ── Response buffer routing (E-001-S-005) ────────────────────────────────
    # Route the upstream response to either the buffered path (≤ 512 KB) or the
    # streaming path (> 512 KB or SSE).  This decision is made before reading any
    # response bytes so that large responses are never fully materialised in memory.
    #
    # AC-E001-07 (response):
    #   ≤ 512 KB → aread() into memory; returned as Response (scan-ready in E-002/E-003)
    #   > 512 KB → StreamingResponse via _stream_response_scan stub (real scan in E-004)
    #   SSE (text/event-stream) → always streaming path (scan window logic in E-004)
    response_media_type: str | None = upstream_response.headers.get("content-type")
    is_sse: bool = response_media_type is not None and "text/event-stream" in response_media_type

    response_content_length_header: str | None = upstream_response.headers.get(
        "content-length"
    )
    large_response: bool = bool(
        response_content_length_header
        and int(response_content_length_header) > MAX_RESPONSE_BUFFER_BYTES
    )

    if is_sse or large_response:
        # SSE stream or declared-large response — do not buffer; stream through.
        if large_response:
            logger.info(
                "response_streaming_large",
                scan_id=scan_id,
                content_length=response_content_length_header,
                limit=MAX_RESPONSE_BUFFER_BYTES,
            )
        return StreamingResponse(
            content=_stream_response_scan(
                upstream_response,
                scan_id,
                scan_pool=getattr(request.app.state, "scan_pool", None),
                audit_backend=getattr(request.app.state, "audit_backend", None),
                user_id=user_id,
                streaming_tracker=getattr(request.app.state, "streaming_tracker", None),
            ),
            status_code=upstream_response.status_code,
            headers=agent_headers,
            media_type=response_media_type,
        )

    # Buffered path: read full response body (≤ 512 KB or no Content-Length hint).
    # AC-E001-03: status code and body forwarded unchanged.
    # AC-E004-06: scan response body before returning (E-004-S-005).
    response_body: bytes = await upstream_response.aread()

    if len(response_body) > MAX_RESPONSE_BUFFER_BYTES:
        # Body exceeded limit without a Content-Length hint (e.g. chunked encoding).
        # Log a warning; E-004 will route these to the streaming scan path instead.
        logger.warning(
            "response_exceeded_buffer_after_read",
            scan_id=scan_id,
            body_size=len(response_body),
            limit=MAX_RESPONSE_BUFFER_BYTES,
        )

    # ── Scan non-streaming response body (AC-E004-06) ────────────────────────
    # Scan the response body using scan_or_block() — same pipeline as request scanning.
    # This provides full Presidio NLP coverage for non-streaming responses.
    # Streaming responses are scanned per-window in _stream_response_scan().
    if response_body:
        response_text = response_body.decode("utf-8", errors="replace")
        response_scan_result = await scan_or_block(
            content=response_text,
            scan_pool=getattr(request.app.state, "scan_pool", None),
            scan_id=scan_id,
            audit_context={
                "scan_id": scan_id,
                "path": path,
                "direction": "RESPONSE",
                "method": request.method,
            },
            latency_tracker=getattr(request.app.state, "latency_tracker", None),
            allowlist_loader=getattr(request.app.state, "allowlist_loader", None),  # E-009
        )
        if response_scan_result.action == Action.BLOCK:
            logger.info(
                "response_body_blocked",
                scan_id=scan_id,
                rule_id=response_scan_result.rule_id,
                risk_level=str(response_scan_result.risk_level),
            )
            return build_block_response(response_scan_result)

    return Response(
        content=response_body,
        status_code=upstream_response.status_code,
        headers=agent_headers,
        media_type=response_media_type,
    )


# ─── SSE Content Extraction ───────────────────────────────────────────────────


def _extract_content_from_sse_message(message: str) -> str:
    """Extract text content from a single SSE message block.

    Handles both OpenAI and Anthropic SSE formats.

    OpenAI format (choices[0].delta.content):
        data: {"choices": [{"delta": {"content": "hello"}, ...}], ...}

    Anthropic format (content_block_delta.delta.text):
        event: content_block_delta
        data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}

    Args:
        message: A single complete SSE message block (lines before the blank separator).

    Returns:
        Extracted text content, or "" if not text content / cannot parse.
    """
    data_line: Optional[str] = None
    for line in message.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            data_line = line[5:].strip()
            break

    if not data_line or data_line == "[DONE]":
        return ""

    try:
        parsed = json.loads(data_line)
    except (json.JSONDecodeError, ValueError):
        return ""

    # OpenAI format: choices[0].delta.content
    if "choices" in parsed:
        choices = parsed.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            return delta.get("content", "") or ""

    # Anthropic format: content_block_delta with text_delta
    if parsed.get("type") == "content_block_delta":
        delta = parsed.get("delta", {})
        if delta.get("type") == "text_delta":
            return delta.get("text", "") or ""

    return ""


# ─── Streaming Audit Helpers ──────────────────────────────────────────────────


async def _log_stream_event(
    audit_backend: Any,
    scan_id: str,
    user_id: str,
    action: str,
    rule_id: Optional[str],
    risk_level_str: Optional[str],
    redacted_excerpt: Optional[str],
    tokens_delivered: int,
    test: bool = False,
    advisory_entities: Optional[list] = None,
) -> None:
    """Async audit logger for streaming events (fire-and-forget via create_task).

    Called via asyncio.create_task() — NEVER awaited directly from the streaming
    generator (that would block I/O on the event loop during streaming).

    Args:
        audit_backend: AuditBackend protocol instance (may be None → no-op).
        scan_id:       ULID for the request.
        user_id:       Authenticated user identifier.
        action:        "ALLOW" or "BLOCK".
        rule_id:       Rule that triggered BLOCK (None for ALLOW).
        risk_level_str: String risk level (None for ALLOW).
        redacted_excerpt: Sanitised excerpt (None for ALLOW).
        tokens_delivered: Byte-approx token count forwarded (streaming only).
        test:          True if the match was a test credential.
        advisory_entities: Presidio advisory entity types detected (if any).
    """
    if audit_backend is None:
        return

    try:
        from app.audit.models import AuditEvent

        risk_level_typed = None
        if risk_level_str in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            risk_level_typed = risk_level_str  # type: ignore[assignment]

        event = AuditEvent(
            scan_id=scan_id,
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            action=action,  # type: ignore[arg-type]
            direction="RESPONSE",
            rule_id=rule_id,
            risk_level=risk_level_typed,
            redacted_excerpt=redacted_excerpt,
            test=test,
            tokens_delivered=tokens_delivered if action == "BLOCK" else None,
            advisory_presidio_entities=advisory_entities,
        )
        await audit_backend.log_event(event)
    except Exception as exc:  # noqa: BLE001
        # Audit logging failures must NEVER affect the streaming response.
        # Log and continue — fail-open for audit, not for security scan.
        logging.getLogger(__name__).error(
            "[%s] Stream audit log failed (non-fatal): %s", scan_id, exc
        )


# ─── Request-Path Audit Helper ────────────────────────────────────────────────


def _audit_request_event(
    backend: Any,
    scan_result: "ScanResult",
    scan_id: str,
    user_id: str,
    path: str,
) -> None:
    """Fire-and-forget audit event for non-streaming request scans (E-009-S-004).

    Logs BLOCK and ALLOW_SUPPRESSED events to the audit backend.
    ALLOW events are NOT logged (they generate no audit trail entry in v1).

    Called via asyncio.create_task() to avoid blocking the request path.
    NEVER raises — audit failures must not affect security enforcement.

    AC-E009-04: ALLOW_SUPPRESSED events are logged (never silently dropped).
    """
    if backend is None:
        return
    if scan_result.action == Action.ALLOW:
        return

    try:
        from app.audit.models import AuditEvent

        event = AuditEvent(
            scan_id=scan_id,
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            action=scan_result.action.value,  # "BLOCK" or "ALLOW_SUPPRESSED"
            direction="REQUEST",
            rule_id=scan_result.rule_id,
            risk_level=scan_result.risk_level.value if scan_result.risk_level else None,
            redacted_excerpt=scan_result.redacted_excerpt,
            allowlist_rule_id=scan_result.allowlist_rule_id,
            test=scan_result.test,
        )
        asyncio.create_task(backend.log_event(event))  # fire-and-forget
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).error(
            "[%s] Request audit log failed (non-fatal): %s", scan_id, exc
        )


# ─── Background Presidio Advisory Scan ────────────────────────────────────────


async def _presidio_advisory_stream_scan(
    text: str,
    scan_pool: ProcessPoolExecutor,
    scan_id: str,
    abort_trigger: asyncio.Event,
    advisory_result: dict,
) -> None:
    """Background Presidio scan on accumulated stream content (non-blocking).

    Launched via asyncio.create_task() after the stream starts. The scan runs
    on the ProcessPoolExecutor (no event loop blocking).

    If PII is detected and the stream is still open (abort_trigger not set):
    sets abort_trigger to signal the forwarding loop to abort.

    Result stored in advisory_result dict for audit event.

    AC-E004-09: PII detected → stream abort triggered within ~2 windows.
    """
    from app.scanner.presidio_worker import presidio_scan_worker

    try:
        loop = asyncio.get_event_loop()
        entities = await asyncio.wait_for(
            loop.run_in_executor(scan_pool, presidio_scan_worker, text),
            timeout=30.0,  # generous budget — advisory doesn't gate response
        )
        advisory_result["entities"] = [e["entity_type"] for e in entities]
        advisory_result["pii_detected"] = bool(entities)

        if entities and not abort_trigger.is_set():
            logger.info(
                "[%s] Presidio advisory scan detected PII in stream: %s",
                scan_id,
                advisory_result["entities"],
            )
            abort_trigger.set()  # Signal stream abort to forwarding loop

    except asyncio.TimeoutError:
        logger.debug("[%s] Presidio advisory stream scan timed out", scan_id)
        advisory_result["pii_detected"] = None

    except Exception as exc:  # noqa: BLE001
        logger.debug("[%s] Presidio advisory stream scan error: %s", scan_id, exc)
        advisory_result["pii_detected"] = None


# ─── Real Streaming Scan Implementation (E-004) ───────────────────────────────


async def _stream_response_scan(
    upstream_response: httpx.Response,
    scan_id: str,
    scan_pool: Optional[ProcessPoolExecutor] = None,
    audit_backend: Optional[Any] = None,
    user_id: str = "unknown",
    streaming_tracker: Optional["StreamingMetricsTracker"] = None,
) -> AsyncGenerator[bytes, None]:
    """Real streaming scan implementation — replaces the E-001 pass-through stub.

    Scans each 512-char SSE content window with regex (google-re2).
    On BLOCK: emits [DONE] + ongarde_block SSE event and closes upstream.
    On PASS: forwards raw SSE bytes to the agent unchanged.

    Background Presidio advisory task runs on the ProcessPoolExecutor for the
    full accumulated buffer (non-blocking — fires and forgets).

    Key design properties:
      - Regex ONLY for window scans — Presidio is strictly advisory background
      - ≤ 0.5ms p99 per window scan (AC-E004-02)
      - Up to 1 window (~128 tokens) may reach agent before abort (AC-E004-08 —
        documented limitation of the window-based model)
      - asyncio.create_task() for all audit writes (never await in generator)
      - try/finally ensures streaming_tracker.stream_closed() always called

    Args:
        upstream_response: Streaming httpx.Response (stream=True on send).
        scan_id:           ULID for this request (for audit correlation).
        scan_pool:         Presidio ProcessPoolExecutor (None → no advisory scan).
        audit_backend:     AuditBackend for log_event() (None → no audit).
        user_id:           Authenticated user ID (for audit event).
        streaming_tracker: StreamingMetricsTracker for /health/scanner metrics.

    Yields:
        Raw bytes chunks — either forwarded SSE bytes (PASS) or abort sequence (BLOCK).
    """
    if streaming_tracker is not None:
        streaming_tracker.stream_opened()

    scanner = StreamingScanner(
        scan_id=scan_id,
        on_window_scan=(
            streaming_tracker.record_window_scan
            if streaming_tracker is not None
            else None
        ),
    )

    # Advisory Presidio scan state (AC-E004-09)
    abort_trigger = asyncio.Event()
    advisory_result: dict = {}
    advisory_task: Optional[asyncio.Task] = None

    logger.info("stream_scan_start", scan_id=scan_id)

    try:
        sse_buffer = ""

        async for chunk in upstream_response.aiter_bytes():
            # ── Check advisory abort trigger between chunks ────────────────
            if abort_trigger.is_set() and not scanner.aborted:
                advisory_scan_result = ScanResult(
                    action=Action.BLOCK,
                    scan_id=scan_id,
                    rule_id="PRESIDIO_STREAM_ADVISORY",
                    risk_level=RiskLevel.HIGH,
                )
                asyncio.create_task(
                    _log_stream_event(
                        audit_backend, scan_id, user_id, "BLOCK",
                        "PRESIDIO_STREAM_ADVISORY", "HIGH",
                        None, scanner.tokens_delivered,
                        advisory_entities=advisory_result.get("entities"),
                    )
                )
                async for abort_bytes in emit_stream_abort(
                    advisory_scan_result, tokens_delivered=scanner.tokens_delivered
                ):
                    yield abort_bytes
                try:
                    await upstream_response.aclose()
                except Exception:  # noqa: BLE001
                    pass
                return

            # ── Accumulate SSE buffer ─────────────────────────────────────
            chunk_str = chunk.decode("utf-8", errors="replace")
            sse_buffer += chunk_str
            pending_to_forward: list[bytes] = []

            # ── Process complete SSE messages ─────────────────────────────
            blocked = False
            while "\n\n" in sse_buffer:
                message, sse_buffer = sse_buffer.split("\n\n", 1)
                content = _extract_content_from_sse_message(message)
                forward_bytes = (message + "\n\n").encode("utf-8")

                if content:
                    scan_result = scanner.add_content(content)
                    if scan_result is not None and scan_result.action == Action.BLOCK:
                        # ── BLOCK detected — do NOT forward this window ───
                        asyncio.create_task(
                            _log_stream_event(
                                audit_backend, scan_id, user_id, "BLOCK",
                                scan_result.rule_id,
                                str(scan_result.risk_level.value) if scan_result.risk_level else "CRITICAL",
                                scan_result.redacted_excerpt,
                                scanner.tokens_delivered,
                                test=scan_result.test,
                            )
                        )
                        if advisory_task is not None:
                            advisory_task.cancel()

                        async for abort_bytes in emit_stream_abort(
                            scan_result, tokens_delivered=scanner.tokens_delivered
                        ):
                            yield abort_bytes
                        try:
                            await upstream_response.aclose()
                        except Exception:  # noqa: BLE001
                            pass
                        blocked = True
                        break
                    # PASS: accumulate forward bytes
                    pending_to_forward.append(forward_bytes)

                    # Launch advisory Presidio task after first full window
                    if (
                        scanner.window_count == 1
                        and advisory_task is None
                        and scan_pool is not None
                        and len(scanner.presidio_accumulation_buffer) > 0
                    ):
                        advisory_task = asyncio.create_task(
                            _presidio_advisory_stream_scan(
                                scanner.presidio_accumulation_buffer,
                                scan_pool,
                                scan_id,
                                abort_trigger,
                                advisory_result,
                            )
                        )
                else:
                    # No text content (metadata SSE message) — always forward
                    pending_to_forward.append(forward_bytes)

            if blocked:
                return

            # ── Forward all pending PASS bytes ────────────────────────────
            for fwd in pending_to_forward:
                yield fwd

        # ── Stream ended — flush remaining buffer ─────────────────────────
        if sse_buffer:
            content = _extract_content_from_sse_message(sse_buffer)
            if content:
                flush_result = scanner.flush()
                if flush_result is not None and flush_result.action == Action.BLOCK:
                    asyncio.create_task(
                        _log_stream_event(
                            audit_backend, scan_id, user_id, "BLOCK",
                            flush_result.rule_id,
                            str(flush_result.risk_level.value) if flush_result.risk_level else "CRITICAL",
                            flush_result.redacted_excerpt,
                            scanner.tokens_delivered,
                            test=flush_result.test,
                        )
                    )
                    if advisory_task is not None:
                        advisory_task.cancel()
                    async for abort_bytes in emit_stream_abort(
                        flush_result, tokens_delivered=scanner.tokens_delivered
                    ):
                        yield abort_bytes
                    return
            yield sse_buffer.encode("utf-8")

        # ── Flush final scanner window ────────────────────────────────────
        else:
            flush_result = scanner.flush()
            if flush_result is not None and flush_result.action == Action.BLOCK:
                asyncio.create_task(
                    _log_stream_event(
                        audit_backend, scan_id, user_id, "BLOCK",
                        flush_result.rule_id,
                        str(flush_result.risk_level.value) if flush_result.risk_level else "CRITICAL",
                        flush_result.redacted_excerpt,
                        scanner.tokens_delivered,
                        test=flush_result.test,
                    )
                )
                if advisory_task is not None:
                    advisory_task.cancel()
                async for abort_bytes in emit_stream_abort(
                    flush_result, tokens_delivered=scanner.tokens_delivered
                ):
                    yield abort_bytes
                return

        # ── Stream completed cleanly — log ALLOW event ────────────────────
        asyncio.create_task(
            _log_stream_event(
                audit_backend, scan_id, user_id, "ALLOW",
                None, None, None, scanner.tokens_delivered,
                advisory_entities=advisory_result.get("entities"),
            )
        )

        logger.info(
            "stream_scan_complete",
            scan_id=scan_id,
            windows_scanned=scanner.window_count,
            tokens_delivered=scanner.tokens_delivered,
            aborted=scanner.aborted,
        )

    finally:
        if streaming_tracker is not None:
            streaming_tracker.stream_closed()
        # Cancel advisory task if still running
        if advisory_task is not None and not advisory_task.done():
            advisory_task.cancel()


# ─── Lifecycle helpers (called from main.py lifespan) ─────────────────────────


async def shutdown_proxy_engine() -> None:
    """No-op stub for backward compatibility with main.py shutdown sequence.

    In E-001-S-002, the http_client lifecycle is managed by main.py's lifespan
    (created at startup, closed at shutdown). This function exists so main.py's
    existing shutdown call site continues to work.
    """
    logger.debug("proxy engine shutdown: http_client managed by lifespan (no-op here)")
