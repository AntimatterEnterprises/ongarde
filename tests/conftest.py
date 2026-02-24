"""Root test configuration for OnGarde.

Sets ONGARDE_AUTH_REQUIRED=false for the entire test suite so that existing
proxy, scanner, and integration tests do not need to provision API keys.

Tests that explicitly verify auth enforcement (test_auth_middleware.py,
test_auth_integration.py) override this with their own monkeypatch fixture
that sets ONGARDE_AUTH_REQUIRED=true.

Production default is ONGARDE_AUTH_REQUIRED=true — see app/auth/middleware.py.
"""

import os
import pytest


@pytest.fixture(autouse=True)
def disable_auth_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable auth enforcement and localhost-only checks for all tests by default.

    Auth-specific test modules override ONGARDE_AUTH_REQUIRED=true via their own
    autouse fixture (runs after this one and wins).

    Dashboard localhost middleware uses TestClient which sets client host to
    'testclient' — disable the check so dashboard tests pass without migration.
    """
    monkeypatch.setenv("ONGARDE_AUTH_REQUIRED", "false")
    monkeypatch.setenv("ONGARDE_DASHBOARD_LOCALHOST_ONLY", "false")


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Reset the in-memory rate limiter storage between tests.

    Prevents test-to-test rate limit bleed where multiple tests hitting the
    same endpoint within the same minute would trigger a 429.
    """
    from app.auth.limiter import limiter
    try:
        # slowapi stores state in the underlying limits library storage backend
        limiter._storage.reset()
    except Exception:
        pass  # Storage may not support reset in all backends — safe to ignore
