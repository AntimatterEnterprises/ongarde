"""Config loading for OnGarde.

Reads `.ongarde/config.yaml` (or `~/.ongarde/config.yaml`).
Raises SystemExit on parse errors or missing `version` field.
If no config file is found, returns default values (safe to run without config).

Config search order (E-001-S-004):
  1. `config_path` argument (if provided — for testing or explicit override)
  2. ONGARDE_CONFIG environment variable (if set)
  3. `.ongarde/config.yaml` (working directory — for development)
  4. `~/.ongarde/config.yaml` (home directory — for production deployments)

Environment variable overrides:
  ONGARDE_PORT — overrides proxy.port (takes precedence over config file value)
  ONGARDE_CONFIG — sets an explicit config file path to try first

Stories: E-001-S-001 (dataclass stubs), E-001-S-004 (complete implementation)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import yaml

from app.constants import DEFAULT_PRESIDIO_SYNC_CAP, INPUT_HARD_CAP
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ─── Version constants ────────────────────────────────────────────────────────

# Current supported config version
SUPPORTED_CONFIG_VERSION = 1

# Set of all supported versions — used for validation in load_config()
SUPPORTED_VERSIONS: frozenset[int] = frozenset({1})

# ─── Validation sets ─────────────────────────────────────────────────────────

# Valid values for scanner.mode
VALID_SCANNER_MODES: frozenset[str] = frozenset({"full", "lite"})

# Default config search paths (ONGARDE_CONFIG env var prepended at runtime)
DEFAULT_CONFIG_PATHS = [
    ".ongarde/config.yaml",
    os.path.expanduser("~/.ongarde/config.yaml"),
]


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class UpstreamConfig:
    """Upstream LLM provider URL configuration.

    openai:    Base URL for OpenAI-compatible endpoints (/v1/chat/completions, etc.)
    anthropic: Base URL for Anthropic Messages API (/v1/messages)
    custom:    Optional custom upstream for Ollama or other local models
               (e.g. "http://localhost:11434")
    """

    openai: str = "https://api.openai.com"
    anthropic: str = "https://api.anthropic.com"
    custom: Optional[str] = None


@dataclass
class ScannerConfig:
    """Scanner subsystem configuration."""

    mode: str = "full"  # "full" | "lite"
    entity_set: list[str] = field(
        default_factory=lambda: [
            "CREDIT_CARD",
            "CRYPTO",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "US_SSN",
        ]
    )
    enable_person_detection: bool = False
    presidio_sync_cap: int = DEFAULT_PRESIDIO_SYNC_CAP  # chars threshold for sync Presidio path (overridden by calibration at startup)
    input_hard_cap: int = INPUT_HARD_CAP         # hard truncation cap before scanning
    presidio_timeout_ms: int = 40                # Presidio sync path timeout


@dataclass
class AuditConfig:
    """Audit backend configuration."""

    retention_days: int = 90
    path: str = "~/.ongarde/audit.db"


@dataclass
class ProxyConfig:
    """Proxy binding configuration."""

    host: str = "127.0.0.1"
    port: int = 4242


@dataclass
class Config:
    """Root configuration object populated from .ongarde/config.yaml.

    All fields have safe defaults — OnGarde can start without any config file.
    """

    version: int = SUPPORTED_CONFIG_VERSION
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    strict_mode: bool = False
    path: Optional[str] = None  # Path to the loaded config file (for hot-reload in E-009)

    @classmethod
    def defaults(cls) -> "Config":
        """Return a fully-default Config (no file required).

        Used when no config file is found (AC-E001-08 #4: missing config = not an error).
        """
        return cls()

    @classmethod
    def from_dict(cls, raw: dict, path: Optional[str] = None) -> "Config":
        """Construct Config from a parsed YAML dict.

        Merges user-supplied values onto defaults; unknown keys are silently ignored.

        Args:
            raw:  Parsed YAML dict (must already be validated for version field).
            path: Path to the config file (stored in Config.path for hot-reload).

        Returns:
            Config with all fields populated from raw + defaults for missing fields.

        Raises:
            SystemExit(1): On invalid scanner.mode value.
        """
        # ── Scanner ───────────────────────────────────────────────────────────
        scanner_raw = raw.get("scanner", {})
        scanner_mode = scanner_raw.get("mode", "full")
        if scanner_mode not in VALID_SCANNER_MODES:
            msg = (
                f"CONFIG ERROR: Invalid scanner.mode: '{scanner_mode}'. "
                f"Supported values: {sorted(VALID_SCANNER_MODES)}."
            )
            print(msg, file=sys.stderr)
            raise SystemExit(1)
        scanner = ScannerConfig(
            mode=scanner_mode,
            entity_set=scanner_raw.get(
                "entity_set",
                ScannerConfig.__dataclass_fields__["entity_set"].default_factory(),  # type: ignore[misc]
            ),
            enable_person_detection=scanner_raw.get("enable_person_detection", False),
            presidio_sync_cap=scanner_raw.get("presidio_sync_cap", DEFAULT_PRESIDIO_SYNC_CAP),
            input_hard_cap=scanner_raw.get("input_hard_cap", INPUT_HARD_CAP),
            presidio_timeout_ms=scanner_raw.get("presidio_timeout_ms", 40),
        )

        # ── Audit ─────────────────────────────────────────────────────────────
        audit_raw = raw.get("audit", {})
        audit = AuditConfig(
            retention_days=audit_raw.get("retention_days", 90),
            path=audit_raw.get("path", "~/.ongarde/audit.db"),
        )

        # ── Proxy ─────────────────────────────────────────────────────────────
        proxy_raw = raw.get("proxy", {})
        proxy = ProxyConfig(
            host=proxy_raw.get("host", "127.0.0.1"),
            port=proxy_raw.get("port", 4242),
        )

        # ── Upstream ──────────────────────────────────────────────────────────
        upstream_raw = raw.get("upstream", {})
        upstream = UpstreamConfig(
            openai=upstream_raw.get("openai", "https://api.openai.com"),
            anthropic=upstream_raw.get("anthropic", "https://api.anthropic.com"),
            custom=upstream_raw.get("custom"),
        )

        return cls(
            version=raw.get("version", SUPPORTED_CONFIG_VERSION),
            upstream=upstream,
            scanner=scanner,
            audit=audit,
            proxy=proxy,
            strict_mode=raw.get("strict_mode", False),
            path=path,
        )


# ─── Config loading ───────────────────────────────────────────────────────────


def load_config(config_path: Optional[str] = None) -> Config:
    """Load and validate OnGarde configuration.

    Search order:
      1. ``config_path`` argument (if provided — for testing or explicit override)
      2. ``ONGARDE_CONFIG`` environment variable (if set)
      3. ``.ongarde/config.yaml`` (current working directory — for development)
      4. ``~/.ongarde/config.yaml`` (home directory — for production deployments)

    If no file is found at any of these paths, returns default Config (not an error).
    If a file is found but invalid, writes error to stderr and raises SystemExit(1).

    After loading (or defaulting), ``ONGARDE_PORT`` env var is applied as an override
    to ``config.proxy.port`` regardless of whether a config file was found.

    Returns:
        Config object with all values populated (file values merged onto defaults).

    Raises:
        SystemExit(1): On YAML parse error, missing ``version`` field, unsupported
                       version, invalid ``scanner.mode``, or invalid ``ONGARDE_PORT``.

    AC-E001-08:
      - Missing version field → SystemExit(1) with human-readable message
      - Unknown version → SystemExit(1) with migration hint
      - Invalid YAML → SystemExit(1) with parse error line number
      - Missing config file → Config.defaults() (NOT an error)
    """
    # Build search list
    search_paths: list[str] = []
    if config_path:
        search_paths.append(config_path)
    env_config = os.environ.get("ONGARDE_CONFIG")
    if env_config:
        search_paths.append(env_config)
    search_paths.extend(DEFAULT_CONFIG_PATHS)

    # Find first existing config file
    found_path: Optional[str] = None
    for candidate in search_paths:
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded):
            found_path = expanded
            break

    # ── No config file found ─────────────────────────────────────────────────
    # AC-E001-08 #4: missing config is NOT an error — use all defaults.
    if found_path is None:
        logger.info("No config file found — using defaults", searched=search_paths)
        config = Config.defaults()
        _apply_env_overrides(config)
        return config

    # ── Parse config file ─────────────────────────────────────────────────────
    logger.info("Loading config", path=found_path)

    try:
        with open(found_path) as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        msg = (
            f"CONFIG ERROR: Failed to parse {found_path}: {exc}\n"
            "OnGarde refuses to start with an invalid config. "
            "Check the YAML syntax and try again."
        )
        print(msg, file=sys.stderr)
        raise SystemExit(1)
    except OSError as exc:
        msg = f"CONFIG ERROR: Could not read {found_path}: {exc}"
        print(msg, file=sys.stderr)
        raise SystemExit(1)

    # Empty file or non-mapping YAML (e.g. plain scalar)
    if not isinstance(raw, dict):
        if raw is None:
            # Empty file — treat as missing version
            msg = (
                f"CONFIG ERROR: {found_path} is missing the required 'version' field.\n"
                "Add 'version: 1' to the top of your config file."
            )
        else:
            msg = (
                f"CONFIG ERROR: {found_path} is not a valid YAML mapping.\n"
                "The config file must be a YAML dictionary at the top level."
            )
        print(msg, file=sys.stderr)
        raise SystemExit(1)

    # ── Version validation ────────────────────────────────────────────────────
    # AC-E001-08 #1: missing version field
    version = raw.get("version")
    if version is None:
        msg = (
            f"CONFIG ERROR: {found_path} is missing the required 'version' field.\n"
            "Add 'version: 1' to the top of your config file."
        )
        print(msg, file=sys.stderr)
        raise SystemExit(1)

    # AC-E001-08 #2: unsupported version
    if version not in SUPPORTED_VERSIONS:
        msg = (
            f"CONFIG ERROR: Unsupported config version: {version}. "
            f"Supported versions: {sorted(SUPPORTED_VERSIONS)}.\n"
            "See the migration guide: https://docs.ongarde.io/config-migration"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(1)

    # ── Parse into Config dataclass (raises SystemExit on invalid scanner.mode) ──
    config = Config.from_dict(raw, path=found_path)

    # ── Apply env var overrides (after file parsing) ──────────────────────────
    _apply_env_overrides(config)

    # ── Security warnings ─────────────────────────────────────────────────────
    # architecture.md §9.3, AC-E001-05 (implementation deferred to E-001-S-005
    # for the hard enforcement; this warning is logged here as it's config-reading logic)
    if config.proxy.host == "0.0.0.0":
        logger.warning(
            "SECURITY WARNING: OnGarde is configured to bind on 0.0.0.0 (all interfaces). "
            "This exposes the proxy to network-accessible clients. "
            "Recommended: use proxy.host: '127.0.0.1' for local-only access."
        )

    # strict_mode stub warning — not implemented in v1
    if config.strict_mode:
        logger.warning("strict_mode is not implemented in v1 — ignored")

    logger.info(
        "Config loaded",
        path=found_path,
        version=config.version,
        scanner_mode=config.scanner.mode,
    )
    return config


def _apply_env_overrides(config: Config) -> None:
    """Apply environment variable overrides to a Config object in-place.

    Currently handles:
      ONGARDE_PORT — overrides config.proxy.port (integer; raises SystemExit(1) if invalid)

    This function is called both for file-loaded and default configs so env vars
    always take precedence over any file value.

    Args:
        config: Config to mutate in-place.

    Raises:
        SystemExit(1): If ONGARDE_PORT is set but not a valid integer.
    """
    env_port = os.environ.get("ONGARDE_PORT")
    if env_port is not None:
        try:
            config.proxy.port = int(env_port)
        except ValueError:
            msg = (
                f"CONFIG ERROR: ONGARDE_PORT environment variable is not a valid "
                f"integer: '{env_port}'"
            )
            print(msg, file=sys.stderr)
            raise SystemExit(1)
