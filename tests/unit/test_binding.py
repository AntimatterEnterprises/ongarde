"""Unit tests for E-001-S-005: Loopback-only binding (AC-E001-05).

Verifies that:
  - Default Config has proxy.host == "127.0.0.1" (not "0.0.0.0")
  - Configuring proxy.host = "0.0.0.0" is allowed (no exception raised)
  - The run.py constants are correctly set
  - The config is the authoritative source for binding host (not just env var)

Architecture reference: architecture.md §9.3
Story: E-001-S-005
"""

from __future__ import annotations

from app.config import Config, ProxyConfig
from app.run import UVICORN_BACKLOG, UVICORN_LIMIT_CONCURRENCY, UVICORN_TIMEOUT_KEEP_ALIVE

# ─── AC-E001-05: Default binding ─────────────────────────────────────────────


class TestDefaultBinding:
    """Verify the default config binds to loopback only."""

    def test_default_proxy_host_is_loopback(self) -> None:
        """Config.defaults() must bind to 127.0.0.1, not 0.0.0.0."""
        config = Config.defaults()
        assert config.proxy.host == "127.0.0.1"

    def test_default_proxy_host_is_not_all_interfaces(self) -> None:
        """Config.defaults() must NOT bind to 0.0.0.0 (all interfaces)."""
        config = Config.defaults()
        assert config.proxy.host != "0.0.0.0"

    def test_default_proxy_port_is_4242(self) -> None:
        """Default port is 4242 per AC-E001-05."""
        config = Config.defaults()
        assert config.proxy.port == 4242

    def test_proxy_config_default_host_is_loopback(self) -> None:
        """ProxyConfig() default should also be 127.0.0.1."""
        proxy = ProxyConfig()
        assert proxy.host == "127.0.0.1"


# ─── AC-E001-05: 0.0.0.0 allowed (but warns) ─────────────────────────────────


class TestAllInterfaceBinding:
    """0.0.0.0 is allowed in config (security warning logged; not blocked)."""

    def test_0000_binding_config_is_accepted(self) -> None:
        """Setting proxy.host = '0.0.0.0' must not raise an exception."""
        # This only tests that the Config object accepts the value.
        # The SECURITY WARNING is logged during load_config() (see test_config.py).
        config = Config(proxy=ProxyConfig(host="0.0.0.0"))
        assert config.proxy.host == "0.0.0.0"

    def test_0000_binding_is_not_the_default(self) -> None:
        """0.0.0.0 requires explicit configuration; it is never the default."""
        config = Config.defaults()
        assert config.proxy.host != "0.0.0.0"

    def test_loopback_and_all_interfaces_are_distinct(self) -> None:
        """127.0.0.1 and 0.0.0.0 are not equal — sanity check."""
        loopback = ProxyConfig(host="127.0.0.1")
        all_ifaces = ProxyConfig(host="0.0.0.0")
        assert loopback.host != all_ifaces.host


# ─── run.py — Uvicorn hardened defaults ──────────────────────────────────────


class TestUvicornHardenedDefaults:
    """Verify hardened uvicorn constants in app/run.py (AC architecture.md §9.3)."""

    def test_limit_concurrency_is_100(self) -> None:
        """Max concurrent connections must be 100 (matches pool size)."""
        assert UVICORN_LIMIT_CONCURRENCY == 100

    def test_backlog_is_50(self) -> None:
        """OS connection backlog must be 50."""
        assert UVICORN_BACKLOG == 50

    def test_timeout_keep_alive_is_5(self) -> None:
        """Keep-alive timeout must be 5 seconds (Slow Loris mitigation)."""
        assert UVICORN_TIMEOUT_KEEP_ALIVE == 5
