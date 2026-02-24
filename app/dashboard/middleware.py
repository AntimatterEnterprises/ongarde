"""Dashboard localhost enforcement middleware for OnGarde.

Ensures all /dashboard/* requests originate from loopback (127.0.0.1 or ::1).
This is a code-level enforcement of the architectural invariant documented in
dashboard/api.py: "localhost binding is the security boundary."

Without this middleware the invariant relies solely on the uvicorn binding
(proxy.host: 127.0.0.1). If a user misconfigures proxy.host: 0.0.0.0, the
dashboard would be network-accessible with zero authentication. This middleware
closes that gap unconditionally.

Returns HTTP 403 for any request whose source IP is not a loopback address.
Non-dashboard routes are passed through unchanged (zero overhead).

Security: SEC-002
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Loopback addresses — IPv4 and IPv6
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def _localhost_check_enabled() -> bool:
    """Return True unless ONGARDE_DASHBOARD_LOCALHOST_ONLY=false.

    Defaults to True (enforced). Set to false only in automated tests.
    NEVER disable in production.
    """
    return os.environ.get("ONGARDE_DASHBOARD_LOCALHOST_ONLY", "true").lower() != "false"

_DASHBOARD_PREFIX = "/dashboard"

_FORBIDDEN_BODY: dict = {
    "error": {
        "message": "Dashboard access is restricted to localhost",
        "code": "forbidden",
    }
}


class DashboardLocalhostMiddleware(BaseHTTPMiddleware):
    """Restrict all /dashboard/* requests to loopback origins.

    Any request whose source IP is not 127.0.0.1 or ::1 receives HTTP 403.
    All other paths pass through unchanged.

    This runs as outermost middleware (registered last in create_app) so it
    fires before any route handler, auth dependency, or body read.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if not request.url.path.startswith(_DASHBOARD_PREFIX):
            return await call_next(request)

        # Skip check if disabled (test environments only)
        if not _localhost_check_enabled():
            return await call_next(request)

        client_host = request.client.host if request.client else None

        if client_host not in _LOOPBACK_HOSTS:
            logger.warning(
                "Dashboard access denied: non-localhost origin",
                client_host=client_host,
                path=request.url.path,
            )
            return JSONResponse(
                status_code=403,
                content=_FORBIDDEN_BODY,
            )

        return await call_next(request)
