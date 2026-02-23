"""Request body size limit middleware for OnGarde — E-001-S-005.

Enforces the 1 MB request body hard cap (AC-E001-07):
  - HTTP 413 is returned for bodies exceeding MAX_REQUEST_BODY_BYTES.
  - The check occurs BEFORE any scan gate invocation or upstream connection.
  - Two-phase check:
      1. Content-Length fast path: reject immediately on oversized header value.
      2. Chunked/streaming slow path: accumulate body with rolling cap; reject
         and close connection if 1 MB is exceeded before read completes.

Body size is enforced here so the proxy engine never sees oversized bodies.
The 413 error format matches the JSON structure specified in story E-001-S-005 AC-5.

Architecture reference: architecture.md §9.3
Story: E-001-S-005
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.constants import MAX_REQUEST_BODY_BYTES
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ─── Error response bodies (defined once to avoid duplication) ────────────────

_PAYLOAD_TOO_LARGE_BODY: dict = {
    "error": {
        "message": "Request body too large. Maximum size: 1MB",
        "code": "payload_too_large",
    }
}

_INVALID_CONTENT_LENGTH_BODY: dict = {
    "error": {
        "message": "Invalid Content-Length header",
        "code": "bad_request",
    }
}


# ─── Middleware ───────────────────────────────────────────────────────────────


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Starlette middleware enforcing a 1 MB request body hard cap.

    Registration (in create_app() in app/main.py):
        application.add_middleware(BodySizeLimitMiddleware)

    The middleware runs before all route handlers, ensuring the body size check
    precedes scan gate invocation and upstream connection establishment.

    AC-E001-07 (request body):
      - Content-Length > MAX_REQUEST_BODY_BYTES  → HTTP 413 (fast path, no body read)
      - Content-Length == MAX_REQUEST_BODY_BYTES → accepted (boundary: ≤ 1 MB passes)
      - No Content-Length, accumulated body > 1 MB → HTTP 413 (rolling cap)
      - No Content-Length, accumulated body ≤ 1 MB → accepted, body cached in request
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        """Enforce body size limit; delegate to the next handler if within limit.

        Args:
            request:   Incoming Starlette/FastAPI request.
            call_next: Next middleware or route handler in the chain.

        Returns:
            HTTP 413 JSONResponse if body exceeds MAX_REQUEST_BODY_BYTES.
            HTTP 400 JSONResponse if Content-Length header is not a valid integer.
            Delegated response from the next handler otherwise.
        """
        content_length_header = request.headers.get("content-length")

        # ── Phase 1: Content-Length fast path ─────────────────────────────────
        # If the client declares the body size upfront, we can reject immediately
        # without reading a single byte from the socket.
        if content_length_header is not None:
            try:
                declared_size = int(content_length_header)
            except ValueError:
                logger.warning(
                    "Invalid Content-Length header",
                    value=content_length_header,
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=400,
                    content=_INVALID_CONTENT_LENGTH_BODY,
                )

            if declared_size > MAX_REQUEST_BODY_BYTES:
                logger.warning(
                    "Request body too large (Content-Length)",
                    declared_size=declared_size,
                    limit=MAX_REQUEST_BODY_BYTES,
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=413,
                    content=_PAYLOAD_TOO_LARGE_BODY,
                )

            # Content-Length is within the limit — proceed without reading the body.
            # The proxy handler will read it via request.body().
            return await call_next(request)

        # ── Phase 2: Chunked / no Content-Length — rolling 1 MB cap ──────────
        # For requests without Content-Length (chunked transfer encoding), we
        # read the body in chunks and reject as soon as the cumulative size
        # exceeds the limit. The accumulated bytes are stored in request._body
        # so the downstream proxy handler can read them without re-consuming the stream.
        body_chunks: list[bytes] = []
        total_size: int = 0

        async for chunk in request.stream():
            total_size += len(chunk)
            if total_size > MAX_REQUEST_BODY_BYTES:
                logger.warning(
                    "Request body too large (chunked)",
                    accumulated_size=total_size,
                    limit=MAX_REQUEST_BODY_BYTES,
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=413,
                    content=_PAYLOAD_TOO_LARGE_BODY,
                )
            body_chunks.append(chunk)

        # Cache the accumulated body in request._body.
        # Starlette's Request.body() checks for this attribute first; setting it
        # here ensures the proxy handler's `await request.body()` returns the cached
        # bytes without attempting to re-read from the (already-consumed) stream.
        request._body = b"".join(body_chunks)  # type: ignore[attr-defined]

        return await call_next(request)
