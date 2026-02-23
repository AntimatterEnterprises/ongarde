"""Unit tests for E-001-S-004: Config file loading, validation, and upstream URL routing.

Covers all Acceptance Criteria from E-001-S-004:

  AC-E001-08 — Config validation and startup refusal:
    #1: Missing 'version' field → SystemExit(1) with human-readable message
    #2: Unknown version (e.g. version: 2) → SystemExit(1) with migration hint
    #3: Invalid YAML syntax → SystemExit(1) with parse error message
    #4: Missing config file → Config.defaults(), no exception
    #5: After successful load, upstream.openai and upstream.anthropic populated

  AC-E001-02 (partial — upstream routing):
    #6: /v1/chat/completions, /v1/completions, /v1/embeddings → openai upstream
    #7: /v1/messages → anthropic upstream
    #8: custom upstream URL configured and stored in UpstreamConfig.custom

  Config structure validation:
    #9:  version: 1 only → all defaults populated
    #10: config.proxy.host defaults to "127.0.0.1"
    #11: config.proxy.port defaults to 4242; ONGARDE_PORT overrides
    #12: config.scanner.mode defaults to "full"; invalid value → SystemExit(1)

Additional:
  - ONGARDE_CONFIG env var support
  - ONGARDE_PORT with no config file (defaults path)
  - proxy.host "0.0.0.0" security warning
  - strict_mode warning
  - UpstreamConfig dataclass: openai, anthropic, custom fields
  - SUPPORTED_VERSIONS constant
"""

from __future__ import annotations

import os
import textwrap
from typing import Any

import pytest

from app.config import (
    SUPPORTED_VERSIONS,
    VALID_SCANNER_MODES,
    AuditConfig,
    Config,
    ProxyConfig,
    ScannerConfig,
    UpstreamConfig,
    load_config,
)
from app.proxy.engine import _route_upstream


# ─── AC-E001-08 #4: Missing config file → Config.defaults() ─────────────────


class TestMissingConfigFile:
    """AC-E001-08 #4: Missing config file is NOT an error — returns defaults."""

    def test_nonexistent_path_returns_defaults(self) -> None:
        """load_config(nonexistent path) returns Config.defaults() without raising."""
        config = load_config(config_path="/nonexistent/path/to/config.yaml")
        assert isinstance(config, Config)
        assert config.version == 1

    def test_missing_file_does_not_raise(self) -> None:
        """No exception raised when config file does not exist."""
        try:
            load_config(config_path="/nonexistent/path/config.yaml")
        except SystemExit:
            pytest.fail("SystemExit raised for missing config file — should not raise")

    def test_missing_file_returns_default_scanner_mode(self) -> None:
        """Missing file → scanner.mode defaults to 'full'."""
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert config.scanner.mode == "full"

    def test_missing_file_returns_default_proxy_host(self) -> None:
        """Missing file → proxy.host defaults to '127.0.0.1'."""
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert config.proxy.host == "127.0.0.1"

    def test_missing_file_returns_default_proxy_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing file → proxy.port defaults to 4242 (when ONGARDE_PORT not set)."""
        monkeypatch.delenv("ONGARDE_PORT", raising=False)
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert config.proxy.port == 4242

    def test_missing_file_returns_default_upstream_openai(self) -> None:
        """Missing file → upstream.openai defaults to 'https://api.openai.com'."""
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert config.upstream.openai == "https://api.openai.com"

    def test_missing_file_returns_default_upstream_anthropic(self) -> None:
        """Missing file → upstream.anthropic defaults to 'https://api.anthropic.com'."""
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert config.upstream.anthropic == "https://api.anthropic.com"

    def test_missing_file_returns_none_upstream_custom(self) -> None:
        """Missing file → upstream.custom defaults to None."""
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert config.upstream.custom is None


# ─── AC-E001-08 #1: Missing version field ────────────────────────────────────


class TestMissingVersionField:
    """AC-E001-08 #1: Config file with missing 'version' field causes SystemExit(1)."""

    def test_missing_version_raises_system_exit(self, tmp_path: Any) -> None:
        """Missing version field → SystemExit raised."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "upstream:\n"
            "  openai: 'https://api.openai.com'\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_missing_version_error_message(
        self, tmp_path: Any, capsys: pytest.CaptureFixture
    ) -> None:
        """Missing version field → human-readable error on stderr."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("scanner:\n  mode: full\n")
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))
        captured = capsys.readouterr()
        assert "CONFIG ERROR" in captured.err
        assert "version" in captured.err.lower()

    def test_empty_file_raises_system_exit(
        self, tmp_path: Any, capsys: pytest.CaptureFixture
    ) -> None:
        """Empty config file (treated as missing version) → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "CONFIG ERROR" in captured.err


# ─── AC-E001-08 #2: Unknown version ──────────────────────────────────────────


class TestUnknownVersion:
    """AC-E001-08 #2: Config file with unsupported version → SystemExit(1)."""

    def test_version_2_raises_system_exit(self, tmp_path: Any) -> None:
        """version: 2 → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 2\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_version_99_raises_system_exit(self, tmp_path: Any) -> None:
        """version: 99 → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 99\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_unknown_version_error_message_contains_migration_hint(
        self, tmp_path: Any, capsys: pytest.CaptureFixture
    ) -> None:
        """Unknown version → stderr message includes migration guide URL."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 99\n")
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))
        captured = capsys.readouterr()
        assert "CONFIG ERROR" in captured.err
        assert "99" in captured.err
        assert "migration" in captured.err.lower()

    def test_unknown_version_error_message_contains_supported_versions(
        self, tmp_path: Any, capsys: pytest.CaptureFixture
    ) -> None:
        """Unknown version → stderr message lists supported versions."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 2\n")
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))
        captured = capsys.readouterr()
        assert "1" in captured.err  # supported version 1 must be mentioned

    def test_version_0_raises_system_exit(self, tmp_path: Any) -> None:
        """version: 0 is also unsupported → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 0\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_supported_versions_constant(self) -> None:
        """SUPPORTED_VERSIONS constant exists and contains 1."""
        assert 1 in SUPPORTED_VERSIONS
        assert isinstance(SUPPORTED_VERSIONS, frozenset)


# ─── AC-E001-08 #3: Invalid YAML syntax ──────────────────────────────────────


class TestInvalidYaml:
    """AC-E001-08 #3: Invalid YAML syntax → SystemExit(1) with parse error."""

    def test_invalid_yaml_raises_system_exit(self, tmp_path: Any) -> None:
        """Invalid YAML → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n  invalid_indent:\nbroken: [unclosed\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_invalid_yaml_error_message(
        self, tmp_path: Any, capsys: pytest.CaptureFixture
    ) -> None:
        """Invalid YAML → stderr message includes CONFIG ERROR and parse info."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n  bad: yaml: [\n")
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))
        captured = capsys.readouterr()
        assert "CONFIG ERROR" in captured.err

    def test_unclosed_bracket_raises_system_exit(self, tmp_path: Any) -> None:
        """Unclosed bracket is invalid YAML → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nlist: [unclosed\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_tab_indentation_raises_system_exit(self, tmp_path: Any) -> None:
        """YAML with tab indentation is invalid → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        # YAML does not allow tab characters as indentation
        config_file.write_bytes(b"version: 1\nscanner:\n\tmode: full\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1


# ─── AC-E001-08 #5 / AC-9: Valid version: 1 → all defaults populated ─────────


class TestValidMinimalConfig:
    """AC-9 / AC-E001-08 #5: Config with version: 1 and no other fields uses all defaults."""

    def test_version_1_only_returns_config(self, tmp_path: Any) -> None:
        """version: 1 with no other fields is a valid config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert isinstance(config, Config)
        assert config.version == 1

    def test_version_1_defaults_upstream_openai(self, tmp_path: Any) -> None:
        """AC-5: upstream.openai populated with default."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.upstream.openai == "https://api.openai.com"

    def test_version_1_defaults_upstream_anthropic(self, tmp_path: Any) -> None:
        """AC-5: upstream.anthropic populated with default."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.upstream.anthropic == "https://api.anthropic.com"

    def test_version_1_defaults_proxy_host(self, tmp_path: Any) -> None:
        """AC-10: proxy.host defaults to '127.0.0.1'."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.proxy.host == "127.0.0.1"

    def test_version_1_defaults_proxy_port(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-11: proxy.port defaults to 4242 when ONGARDE_PORT not set."""
        monkeypatch.delenv("ONGARDE_PORT", raising=False)
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.proxy.port == 4242

    def test_version_1_defaults_scanner_mode(self, tmp_path: Any) -> None:
        """AC-12: scanner.mode defaults to 'full'."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.scanner.mode == "full"

    def test_version_1_stores_config_path(self, tmp_path: Any) -> None:
        """Config.path is set to the file path after successful load."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.path == str(config_file)


# ─── AC-11: ONGARDE_PORT env var ─────────────────────────────────────────────


class TestOngardePortEnvVar:
    """AC-11: ONGARDE_PORT env var overrides proxy.port (takes precedence over config)."""

    def test_ongarde_port_5000_overrides_default(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ONGARDE_PORT=5000 → config.proxy.port == 5000 (file has no port)."""
        monkeypatch.setenv("ONGARDE_PORT", "5000")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.proxy.port == 5000

    def test_ongarde_port_overrides_file_value(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ONGARDE_PORT=9999 overrides proxy.port: 8888 from config file."""
        monkeypatch.setenv("ONGARDE_PORT", "9999")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nproxy:\n  port: 8888\n")
        config = load_config(config_path=str(config_file))
        assert config.proxy.port == 9999

    def test_ongarde_port_without_config_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ONGARDE_PORT=3000 applies even when no config file exists."""
        monkeypatch.setenv("ONGARDE_PORT", "3000")
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert config.proxy.port == 3000

    def test_ongarde_port_invalid_value_raises_system_exit(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ONGARDE_PORT=abc → SystemExit(1) with invalid integer message."""
        monkeypatch.setenv("ONGARDE_PORT", "abc")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_ongarde_port_invalid_error_message(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """ONGARDE_PORT=abc → stderr includes CONFIG ERROR and 'abc'."""
        monkeypatch.setenv("ONGARDE_PORT", "abc")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))
        captured = capsys.readouterr()
        assert "CONFIG ERROR" in captured.err
        assert "abc" in captured.err

    def test_ongarde_port_invalid_no_config_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ONGARDE_PORT=bad applies even without a config file → SystemExit(1)."""
        monkeypatch.setenv("ONGARDE_PORT", "bad")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path="/nonexistent/path/config.yaml")
        assert exc_info.value.code == 1


# ─── AC-E001-02 (partial): Upstream URL configuration ────────────────────────


class TestUpstreamUrlConfiguration:
    """AC-E001-02: Upstream URL values from config file used for routing."""

    def test_custom_openai_url_used(self, tmp_path: Any) -> None:
        """config.upstream.openai uses file value when specified."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 1\n"
            "upstream:\n"
            "  openai: 'http://my-openai-proxy:8080'\n"
        )
        config = load_config(config_path=str(config_file))
        assert config.upstream.openai == "http://my-openai-proxy:8080"

    def test_custom_anthropic_url_used(self, tmp_path: Any) -> None:
        """config.upstream.anthropic uses file value when specified."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 1\n"
            "upstream:\n"
            "  anthropic: 'http://my-anthropic-proxy:9090'\n"
        )
        config = load_config(config_path=str(config_file))
        assert config.upstream.anthropic == "http://my-anthropic-proxy:9090"

    def test_custom_upstream_stored(self, tmp_path: Any) -> None:
        """AC-8: custom upstream URL stored in config.upstream.custom."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 1\n"
            "upstream:\n"
            "  custom: 'http://localhost:11434'\n"
        )
        config = load_config(config_path=str(config_file))
        assert config.upstream.custom == "http://localhost:11434"

    def test_custom_upstream_none_when_not_set(self, tmp_path: Any) -> None:
        """upstream.custom is None when not specified in config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.upstream.custom is None

    def test_upstream_is_upstreamconfig_instance(self, tmp_path: Any) -> None:
        """config.upstream is an UpstreamConfig dataclass, not a dict."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert isinstance(config.upstream, UpstreamConfig)


# ─── AC-E001-02 (partial): Upstream routing ──────────────────────────────────


class TestUpstreamRouting:
    """AC-E001-02: _route_upstream() routes to correct upstream based on path."""

    def test_chat_completions_routes_to_openai(self) -> None:
        """AC-6: /v1/chat/completions → config.upstream.openai."""
        config = Config.defaults()
        result = _route_upstream("v1/chat/completions", config)
        assert result == config.upstream.openai

    def test_completions_routes_to_openai(self) -> None:
        """AC-6: /v1/completions → config.upstream.openai."""
        config = Config.defaults()
        result = _route_upstream("v1/completions", config)
        assert result == config.upstream.openai

    def test_embeddings_routes_to_openai(self) -> None:
        """AC-6: /v1/embeddings → config.upstream.openai."""
        config = Config.defaults()
        result = _route_upstream("v1/embeddings", config)
        assert result == config.upstream.openai

    def test_messages_routes_to_anthropic(self) -> None:
        """AC-7: /v1/messages → config.upstream.anthropic."""
        config = Config.defaults()
        result = _route_upstream("v1/messages", config)
        assert result == config.upstream.anthropic

    def test_messages_routes_to_custom_anthropic_url(self, tmp_path: Any) -> None:
        """AC-7: /v1/messages → uses configured anthropic URL, not hardcoded default."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 1\nupstream:\n  anthropic: 'http://local-anthropic:9090'\n"
        )
        config = load_config(config_path=str(config_file))
        result = _route_upstream("v1/messages", config)
        assert result == "http://local-anthropic:9090"

    def test_chat_completions_uses_custom_openai_url(self, tmp_path: Any) -> None:
        """AC-6: /v1/chat/completions → uses configured openai URL, not hardcoded default."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 1\nupstream:\n  openai: 'http://ollama:11434'\n"
        )
        config = load_config(config_path=str(config_file))
        result = _route_upstream("v1/chat/completions", config)
        assert result == "http://ollama:11434"

    def test_messages_returns_anthropic_base_url(self) -> None:
        """_route_upstream('v1/messages', config) returns config.upstream.anthropic."""
        config = Config.defaults()
        assert _route_upstream("v1/messages", config) == "https://api.anthropic.com"

    def test_chat_completions_returns_openai_base_url(self) -> None:
        """_route_upstream('v1/chat/completions', config) returns config.upstream.openai."""
        config = Config.defaults()
        assert _route_upstream("v1/chat/completions", config) == "https://api.openai.com"


# ─── AC-12: scanner.mode validation ──────────────────────────────────────────


class TestScannerModeValidation:
    """AC-12: scanner.mode accepts 'full' and 'lite'; rejects unknown values."""

    def test_scanner_mode_full_accepted(self, tmp_path: Any) -> None:
        """scanner.mode: 'full' is accepted."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nscanner:\n  mode: full\n")
        config = load_config(config_path=str(config_file))
        assert config.scanner.mode == "full"

    def test_scanner_mode_lite_accepted(self, tmp_path: Any) -> None:
        """scanner.mode: 'lite' is accepted."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nscanner:\n  mode: lite\n")
        config = load_config(config_path=str(config_file))
        assert config.scanner.mode == "lite"

    def test_scanner_mode_invalid_raises_system_exit(self, tmp_path: Any) -> None:
        """scanner.mode: 'turbo' is invalid → SystemExit(1)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nscanner:\n  mode: turbo\n")
        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))
        assert exc_info.value.code == 1

    def test_scanner_mode_invalid_error_message(
        self, tmp_path: Any, capsys: pytest.CaptureFixture
    ) -> None:
        """Invalid scanner.mode → stderr mentions 'scanner.mode' and invalid value."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nscanner:\n  mode: unknown\n")
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))
        captured = capsys.readouterr()
        assert "CONFIG ERROR" in captured.err
        assert "scanner.mode" in captured.err

    def test_valid_scanner_modes_constant(self) -> None:
        """VALID_SCANNER_MODES contains exactly 'full' and 'lite'."""
        assert "full" in VALID_SCANNER_MODES
        assert "lite" in VALID_SCANNER_MODES
        assert isinstance(VALID_SCANNER_MODES, frozenset)


# ─── ONGARDE_CONFIG env var support ──────────────────────────────────────────


class TestOngardeConfigEnvVar:
    """ONGARDE_CONFIG env var: sets an explicit config file path."""

    def test_ongarde_config_env_var_used(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ONGARDE_CONFIG env var points to a valid config file → loaded correctly."""
        config_file = tmp_path / "my_config.yaml"
        config_file.write_text("version: 1\nproxy:\n  port: 7777\n")
        monkeypatch.setenv("ONGARDE_CONFIG", str(config_file))
        monkeypatch.delenv("ONGARDE_PORT", raising=False)
        # Pass nonexistent explicit path — env var should win over DEFAULT_CONFIG_PATHS
        config = load_config(config_path="/nonexistent/explicit.yaml")
        assert config.proxy.port == 7777

    def test_ongarde_config_nonexistent_falls_through_to_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ONGARDE_CONFIG pointing to nonexistent file → falls through to defaults."""
        monkeypatch.setenv("ONGARDE_CONFIG", "/nonexistent/ongarde_config.yaml")
        monkeypatch.delenv("ONGARDE_PORT", raising=False)
        config = load_config(config_path="/nonexistent/explicit.yaml")
        assert isinstance(config, Config)
        assert config.proxy.port == 4242  # default


# ─── Proxy host security warning ─────────────────────────────────────────────


class TestProxyHostWarning:
    """proxy.host: '0.0.0.0' logs a security warning (does NOT prevent startup)."""

    def test_0_0_0_0_binding_does_not_prevent_startup(
        self, tmp_path: Any
    ) -> None:
        """proxy.host: '0.0.0.0' is accepted — warning only, not an error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nproxy:\n  host: '0.0.0.0'\n")
        # Should not raise
        config = load_config(config_path=str(config_file))
        assert config.proxy.host == "0.0.0.0"

    def test_0_0_0_0_binding_returns_config(self, tmp_path: Any) -> None:
        """proxy.host: '0.0.0.0' → Config returned with correct host value."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nproxy:\n  host: '0.0.0.0'\n")
        config = load_config(config_path=str(config_file))
        assert isinstance(config, Config)
        assert config.proxy.host == "0.0.0.0"


# ─── strict_mode stub ─────────────────────────────────────────────────────────


class TestStrictModeStub:
    """strict_mode: true is parsed from config but always treated as a stub (v1)."""

    def test_strict_mode_false_by_default(self, tmp_path: Any) -> None:
        """strict_mode defaults to False."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")
        config = load_config(config_path=str(config_file))
        assert config.strict_mode is False

    def test_strict_mode_true_parsed_but_does_not_error(self, tmp_path: Any) -> None:
        """strict_mode: true is accepted (warning logged, not an error)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\nstrict_mode: true\n")
        # Should not raise — strict_mode is a stub in v1
        config = load_config(config_path=str(config_file))
        assert config.strict_mode is True


# ─── Config dataclass structure ───────────────────────────────────────────────


class TestConfigDataclassStructure:
    """Verify Config and sub-dataclass structure matches the story spec."""

    def test_config_defaults_instance(self) -> None:
        """Config.defaults() returns a Config instance."""
        config = Config.defaults()
        assert isinstance(config, Config)

    def test_config_upstream_is_upstreamconfig(self) -> None:
        """Config.upstream is an UpstreamConfig instance."""
        config = Config.defaults()
        assert isinstance(config.upstream, UpstreamConfig)

    def test_config_proxy_is_proxyconfig(self) -> None:
        """Config.proxy is a ProxyConfig instance."""
        config = Config.defaults()
        assert isinstance(config.proxy, ProxyConfig)

    def test_config_scanner_is_scannerconfig(self) -> None:
        """Config.scanner is a ScannerConfig instance."""
        config = Config.defaults()
        assert isinstance(config.scanner, ScannerConfig)

    def test_config_audit_is_auditconfig(self) -> None:
        """Config.audit is an AuditConfig instance."""
        config = Config.defaults()
        assert isinstance(config.audit, AuditConfig)

    def test_upstreamconfig_has_openai_field(self) -> None:
        """UpstreamConfig has an 'openai' field."""
        upstream = UpstreamConfig()
        assert hasattr(upstream, "openai")
        assert upstream.openai == "https://api.openai.com"

    def test_upstreamconfig_has_anthropic_field(self) -> None:
        """UpstreamConfig has an 'anthropic' field."""
        upstream = UpstreamConfig()
        assert hasattr(upstream, "anthropic")
        assert upstream.anthropic == "https://api.anthropic.com"

    def test_upstreamconfig_has_custom_field(self) -> None:
        """UpstreamConfig has a 'custom' field defaulting to None."""
        upstream = UpstreamConfig()
        assert hasattr(upstream, "custom")
        assert upstream.custom is None

    def test_config_path_field_none_by_default(self) -> None:
        """Config.path defaults to None (no file loaded)."""
        config = Config.defaults()
        assert config.path is None


# ─── Full config YAML with all sections ──────────────────────────────────────


class TestFullConfig:
    """Integration tests for a fully-specified config.yaml file."""

    def test_full_config_loaded_correctly(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All config sections read correctly from a fully-specified config file."""
        monkeypatch.delenv("ONGARDE_PORT", raising=False)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            version: 1

            upstream:
              openai: "http://openai-proxy:8080"
              anthropic: "http://anthropic-proxy:9090"
              custom: "http://ollama:11434"

            proxy:
              host: "127.0.0.1"
              port: 5555

            scanner:
              mode: "lite"
              enable_person_detection: true

            audit:
              retention_days: 30
              path: "/tmp/audit.db"

            strict_mode: false
        """))
        config = load_config(config_path=str(config_file))

        assert config.version == 1
        assert config.upstream.openai == "http://openai-proxy:8080"
        assert config.upstream.anthropic == "http://anthropic-proxy:9090"
        assert config.upstream.custom == "http://ollama:11434"
        assert config.proxy.host == "127.0.0.1"
        assert config.proxy.port == 5555
        assert config.scanner.mode == "lite"
        assert config.scanner.enable_person_detection is True
        assert config.audit.retention_days == 30
        assert config.audit.path == "/tmp/audit.db"
        assert config.strict_mode is False
        assert config.path == str(config_file)

    def test_unknown_top_level_keys_ignored(self, tmp_path: Any) -> None:
        """Unknown keys in config file are silently ignored (forward-compat)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 1\n"
            "unknown_future_key: some_value\n"
            "another_unknown: 42\n"
        )
        # Should not raise
        config = load_config(config_path=str(config_file))
        assert config.version == 1
