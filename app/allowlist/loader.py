"""Allowlist config loader for OnGarde — E-009-S-001.

Loads `.ongarde/allowlist.yaml` (preferred) or the `allowlist:` section of
`.ongarde/config.yaml`. Provides async watchfiles hot-reload; changes apply
within 1 second of file save without restarting the proxy.

IMPORT RULES:
  - `import re2` ONLY — `import re` is PROHIBITED in this file.
  - CI lint gate: grep -r "^import re$|^from re import|^import re " app/

Architecture reference: architecture.md §3.4 Step 4, §E-009
Stories: E-009-S-001 (loader + hot-reload), E-009-S-002 (matcher)
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import re2  # noqa: F401 — google-re2. NEVER: import re
import yaml

from app.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ─── YAML schema constants ────────────────────────────────────────────────────

# Scopes supported in v1 (upstream_path is parsed but treated as global)
_VALID_SCOPES = frozenset({"global", "upstream_path"})

# rule_ids that must never appear in an allowlist (system failures — unsuppressible)
_SYSTEM_RULE_IDS = frozenset({
    "SCANNER_ERROR",
    "SCANNER_TIMEOUT",
    "QUOTA_EXCEEDED",
    "SCANNER_UNAVAILABLE",
})


# ─── AllowlistEntry dataclass ─────────────────────────────────────────────────


@dataclass
class AllowlistEntry:
    """A single allowlist suppression rule.

    Fields:
        rule_id:  Required. The scanner rule_id to suppress (e.g. CREDENTIAL_DETECTED).
        note:     Optional. Human-readable reason for suppression.
        pattern:  Optional. google-re2 pattern — only suppress if content matches.
        scope:    "global" (default) or "upstream_path" (v1: treated as global).

    INVARIANT: pattern (if set) is a valid google-re2 pattern.
    System rule_ids (SCANNER_ERROR, SCANNER_TIMEOUT, etc.) are allowed in the config
    but will never match any scan result (no rule_id generated for system failures
    should be suppressible — defense-in-depth).
    """

    rule_id: str
    note: Optional[str] = None
    pattern: Optional[str] = None       # google-re2 pattern (optional)
    scope: str = "global"               # "global" | "upstream_path" (v1: global only)


# ─── AllowlistLoader ─────────────────────────────────────────────────────────


class AllowlistLoader:
    """Thread-safe, async-compatible allowlist loader with watchfiles hot-reload.

    Usage (in lifespan):
        loader = AllowlistLoader()
        loader.load("/path/to/allowlist.yaml")
        app.state.allowlist_loader = loader
        asyncio.create_task(loader.start_watcher("/path/to/allowlist.yaml"))

    Thread-safety:
        get_entries() uses a threading.Lock for safe concurrent reads from
        the FastAPI async event loop. load() acquires the same lock.
    """

    def __init__(self) -> None:
        self._entries: list[AllowlistEntry] = []
        self._lock = threading.Lock()
        self._watcher_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # ── Public read API ───────────────────────────────────────────────────────

    def get_entries(self) -> list[AllowlistEntry]:
        """Return a snapshot of the current allowlist entries (thread-safe, no I/O).

        Returns a copy — mutations to the returned list do not affect internal state.
        """
        with self._lock:
            return list(self._entries)

    # ── Load from file ────────────────────────────────────────────────────────

    def load(self, path: str) -> int:
        """Load allowlist from a YAML file.

        Returns the number of loaded entries (≥ 0).
        Returns 0 if the file does not exist (not an error).
        Returns -1 on YAML parse / read error (prior allowlist unchanged).

        Never raises — AC-S001-03.
        """
        try:
            with open(path) as fh:
                raw = yaml.safe_load(fh)
        except FileNotFoundError:
            logger.debug("Allowlist file not found — empty allowlist", path=path)
            with self._lock:
                self._entries = []
            return 0
        except yaml.YAMLError as exc:
            logger.error(
                "Config reload failed: YAML parse error — keeping prior allowlist",
                path=path,
                error=str(exc),
            )
            return -1
        except OSError as exc:
            logger.error(
                "Config reload failed: could not read file — keeping prior allowlist",
                path=path,
                error=str(exc),
            )
            return -1

        entries = _parse_allowlist_raw(raw)
        with self._lock:
            self._entries = entries
        logger.debug("Allowlist loaded", count=len(entries), path=path)
        return len(entries)

    def load_from_config(self, config: object) -> int:
        """Load allowlist from the `allowlist:` section of a Config object.

        Used as fallback when no separate allowlist.yaml file exists.
        Reads raw_allowlist entries from config.allowlist (if present).
        Returns entry count. Never raises.
        """
        try:
            raw_entries = getattr(config, "allowlist_entries", None)
            if not raw_entries:
                with self._lock:
                    self._entries = []
                return 0
            entries = _parse_entries(raw_entries)
            with self._lock:
                self._entries = entries
            logger.debug("Allowlist loaded from config", count=len(entries))
            return len(entries)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "load_from_config failed — keeping prior allowlist",
                error=str(exc),
            )
            return -1

    # ── Hot-reload watcher ────────────────────────────────────────────────────

    async def start_watcher(self, path: str) -> None:
        """Async watchfiles watcher — reloads allowlist on file change.

        DESIGN:
        - Uses watchfiles.awatch() — non-blocking async generator.
        - On each change event, calls self.load(path).
        - On successful reload (count >= 0), calls notify_config_reloaded().
        - Designed to run as an asyncio.Task (cancelled on shutdown).
        - Hot-reload completes within 1 second of file save (watchfiles latency ~100ms).

        AC-S001-04: No blocking I/O on the event loop.
        AC-S001-05: Calls notify_config_reloaded() after successful reload.
        AC-S001-06: Invalid YAML → ERROR log, prior allowlist kept, no crash.
        """
        try:
            import watchfiles

            logger.info("Config file watcher started", path=path)
            async for _ in watchfiles.awatch(path):
                try:
                    count = self.load(path)
                    if count >= 0:
                        # Successful reload — notify dashboard (lazy import to avoid circular)
                        try:
                            from app.dashboard.api import notify_config_reloaded
                            notify_config_reloaded(count)
                        except ImportError:
                            pass  # Dashboard not mounted — ignore
                        logger.info(
                            "Allowlist hot-reloaded",
                            count=count,
                            path=path,
                        )
                    # count == -1 means error was already logged in load()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Hot-reload handler error (non-fatal)",
                        error=str(exc),
                        path=path,
                    )
        except asyncio.CancelledError:
            logger.debug("Config file watcher cancelled", path=path)
            raise
        except ImportError:
            logger.warning(
                "watchfiles not available — hot-reload disabled. "
                "Install with: pip install watchfiles"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Config file watcher error (watcher stopped)",
                error=str(exc),
                path=path,
            )


# ─── Parsing helpers ──────────────────────────────────────────────────────────


def _parse_allowlist_raw(raw: object) -> list[AllowlistEntry]:
    """Parse a top-level YAML object into a list of AllowlistEntry objects.

    Handles two YAML structures:
      1. Direct list: [{rule_id: ..., note: ...}, ...]
      2. Mapping with allowlist key: {version: 1, allowlist: [...]}
    """
    if raw is None:
        return []

    if isinstance(raw, list):
        # Top-level list (bare allowlist.yaml with just entries)
        return _parse_entries(raw)

    if isinstance(raw, dict):
        # Config.yaml style: {version: 1, allowlist: [...]}
        entries_raw = raw.get("allowlist", [])
        if entries_raw is None:
            return []
        if not isinstance(entries_raw, list):
            logger.warning(
                "allowlist key is not a list — ignoring",
                actual_type=type(entries_raw).__name__,
            )
            return []
        return _parse_entries(entries_raw)

    logger.warning(
        "Allowlist YAML root is neither a list nor a mapping — empty allowlist",
        actual_type=type(raw).__name__,
    )
    return []


def _parse_entries(raw_list: list) -> list[AllowlistEntry]:
    """Parse a list of raw YAML dicts into AllowlistEntry objects.

    Skips invalid entries with a WARNING (never crashes).
    """
    entries: list[AllowlistEntry] = []

    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            logger.warning(
                "Allowlist entry is not a mapping — skipping",
                index=i,
                actual_type=type(item).__name__,
            )
            continue

        rule_id = item.get("rule_id")
        if not rule_id or not isinstance(rule_id, str):
            logger.warning(
                "Allowlist entry missing rule_id — skipping",
                index=i,
                entry=item,
            )
            continue

        # scope validation
        scope = item.get("scope", "global")
        if scope not in _VALID_SCOPES:
            logger.warning(
                "Unknown scope — treating as global",
                rule_id=rule_id,
                scope=scope,
            )
            scope = "global"
        elif scope == "upstream_path":
            logger.warning(
                "scope 'upstream_path' not yet enforced in v1 — treated as global",
                rule_id=rule_id,
            )

        # pattern validation (must be valid re2 regex)
        pattern = item.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                logger.warning(
                    "Allowlist pattern is not a string — ignoring pattern",
                    rule_id=rule_id,
                    pattern=pattern,
                )
                pattern = None
            else:
                try:
                    re2.compile(pattern)  # Validate at load time (AC-S002-08)
                except re2.error as exc:
                    logger.warning(
                        "Allowlist pattern is not a valid google-re2 regex — ignoring pattern",
                        rule_id=rule_id,
                        pattern=pattern,
                        error=str(exc),
                    )
                    pattern = None

        entry = AllowlistEntry(
            rule_id=rule_id,
            note=item.get("note"),
            pattern=pattern,
            scope=scope,
        )
        entries.append(entry)

    return entries
