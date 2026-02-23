"""HTTP header processing for the OnGarde proxy — E-001-S-003.

Implements all header manipulation rules for upstream-bound requests and
agent-facing responses:

  - build_upstream_headers(): strips OnGarde auth headers, strips hop-by-hop headers,
    injects X-OnGarde-Scan-ID, forwards all remaining request headers unchanged.

  - build_agent_response_headers(): strips hop-by-hop headers from upstream responses,
    forwards all remaining response headers (including rate-limit headers) unchanged.

RFC 7230 §6.1 — hop-by-hop headers MUST NOT be forwarded by intermediaries.

Header constants defined here are imported by engine.py so there is a single source
of truth — no duplication.
"""

from __future__ import annotations

from typing import Iterable

import httpx

# ─── Constants ────────────────────────────────────────────────────────────────

# Hop-by-hop headers MUST be stripped before forwarding (RFC 7230 §6.1).
# httpx sets content-length automatically from the content= parameter.
# host is derived from the upstream URL — do not forward the agent-facing host.
#
# NOTE: This constant is imported by app/proxy/engine.py — it is the single
# canonical definition. Do not redeclare it there.
HOP_BY_HOP_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",  # let httpx compute from content=
    }
)

# OnGarde-specific headers consumed at the proxy boundary — never forwarded upstream.
# Includes the explicit header and the Authorization-based variant.
_ONGARDE_KEY_HEADER: str = "x-ongarde-key"
_ONGARDE_BEARER_PREFIX: str = "Bearer ong-"

# ─── Public API ───────────────────────────────────────────────────────────────


def build_upstream_headers(
    request_headers: Iterable[tuple[str, str]],
    scan_id: str,
) -> dict[str, str]:
    """Build the header dict to send to the upstream LLM provider.

    Rules applied (in order):
      1. Strip ``X-OnGarde-Key`` — the OnGarde authentication header; consumed at
         the proxy boundary, NEVER forwarded to the upstream LLM.
      2. Strip ``Authorization: Bearer ong-*`` — OnGarde key submitted via the
         Authorization header; consumed and stripped.
      3. Strip hop-by-hop headers (RFC 7230 §6.1): connection, keep-alive, host,
         content-length, transfer-encoding, upgrade, te, trailers,
         proxy-authenticate, proxy-authorization.
      4. Forward all remaining headers unchanged (content-type, authorization with
         non-ong bearer, user-agent, anthropic-*, openai-*, x-request-id, etc.).
      5. Inject ``X-OnGarde-Scan-ID: <scan_id>`` — ULID for this request, used as
         the tracing identifier in logs and audit events.

    Important: ``Authorization: Bearer sk-...`` (LLM provider key) is forwarded
    unchanged — only OnGarde's own ``Bearer ong-*`` prefix is stripped.

    Args:
        request_headers: Iterable of (name, value) tuples from the incoming request.
                         Typically ``request.headers.items()`` in FastAPI handlers.
        scan_id:         ULID string generated at proxy handler entry point.
                         Injected as ``X-OnGarde-Scan-ID`` on the upstream request.

    Returns:
        ``dict[str, str]`` — the headers to include in the upstream HTTP request.
    """
    headers: dict[str, str] = {}

    for name, value in request_headers:
        lower_name = name.lower()

        # 1. Strip the explicit OnGarde API key header (never forwarded upstream)
        if lower_name == _ONGARDE_KEY_HEADER:
            continue

        # 2. Strip Authorization if it carries an OnGarde key (Bearer ong-...)
        #    Non-ong Authorization headers (e.g., Bearer sk-openai-...) pass through.
        if lower_name == "authorization" and value.startswith(_ONGARDE_BEARER_PREFIX):
            continue

        # 3. Strip hop-by-hop headers
        if lower_name in HOP_BY_HOP_HEADERS:
            continue

        headers[name] = value

    # 5. Inject the per-request scan ID — used for tracing and audit correlation
    headers["X-OnGarde-Scan-ID"] = scan_id

    return headers


def build_agent_response_headers(
    upstream_headers: httpx.Headers,
) -> dict[str, str]:
    """Build the header dict to return to the agent from the upstream response.

    Rules applied:
      1. Strip hop-by-hop headers (RFC 7230 §6.1) — managed by httpx / Starlette.
      2. Forward all remaining upstream response headers unchanged, including:
           - ``x-ratelimit-limit-requests``
           - ``x-ratelimit-remaining-requests``
           - ``x-ratelimit-limit-tokens``
           - ``x-ratelimit-remaining-tokens``
           - ``retry-after``
           - ``content-type``
           - Any ``anthropic-*``, ``openai-*``, ``cf-*`` headers

    Rate-limit headers are critical for agents to implement correct backoff;
    they must pass through with their exact values unchanged.

    Args:
        upstream_headers: Response headers from the upstream LLM provider
                          (``httpx.Response.headers``).

    Returns:
        ``dict[str, str]`` — the response headers to include in the agent-facing
        :class:`fastapi.responses.StreamingResponse`.
    """
    headers: dict[str, str] = {}
    for name, value in upstream_headers.items():
        if name.lower() in HOP_BY_HOP_HEADERS:
            continue
        headers[name] = value
    return headers
