"""Shared rate limiter for OnGarde auth/key-management endpoints — SEC-007.

Uses slowapi (Starlette-compatible rate limiting) to cap key management
operations. Since all dashboard requests are localhost-only (DashboardLocalhostMiddleware),
this functions as a global cap rather than per-user — 10 key operations per minute
is more than sufficient for legitimate use.

The Limiter instance is created here and shared between:
  - app/auth/router.py  (route decorators)
  - app/main.py         (app.state.limiter + SlowAPIMiddleware registration)
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Module-level limiter — imported by main.py and auth/router.py
limiter = Limiter(key_func=get_remote_address)

# Default rate limit for all key management endpoints
KEY_MANAGEMENT_RATE_LIMIT = "20/minute"
