"""AuditBackend Protocol + EventFilters dataclass.

AuditEvent is defined in app/audit/models.py.
This module defines the pluggable backend interface (AuditBackend Protocol)
and the query filter dataclass (EventFilters).

Layout (architecture.md §10.1):
    models.py   — AuditEvent + type aliases
    protocol.py — AuditBackend Protocol + EventFilters + NullAuditBackend stub
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from app.audit.models import AuditEvent  # noqa: F401 — re-exported via __init__
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ─── EventFilters ─────────────────────────────────────────────────────────────


@dataclass
class EventFilters:
    """Query filters for AuditBackend.query_events() and count_events().

    action_in takes precedence over action when set — used for multi-value
    action filter (e.g., action_in=["BLOCK", "ALLOW_SUPPRESSED"] for the
    dashboard Recent Events list, per AC-FP-005).

    All fields are optional (default None / default value). An empty
    EventFilters() returns all events up to limit=50.
    """

    action: Optional[str] = None
    """Single action filter. Ignored when action_in is set."""
    direction: Optional[str] = None
    """Filter by direction: 'REQUEST' or 'RESPONSE'."""
    user_id: Optional[str] = None
    """Filter to events for a specific user ID."""
    since: Optional[datetime] = None
    """Include events with timestamp >= since (UTC)."""
    until: Optional[datetime] = None
    """Include events with timestamp <= until (UTC)."""
    limit: int = 50
    """Maximum number of events to return (pagination page size)."""
    offset: int = 0
    """Number of events to skip (pagination offset)."""
    test: Optional[bool] = None
    """If set, filter by test flag (True = test events only, False = real events only)."""
    risk_level: Optional[str] = None
    """Filter by risk level: 'CRITICAL', 'HIGH', 'MEDIUM', or 'LOW'."""
    action_in: Optional[list[str]] = None
    """Multi-value action filter. When set, takes precedence over `action`."""


# ─── AuditBackend Protocol ────────────────────────────────────────────────────


@runtime_checkable
class AuditBackend(Protocol):
    """Pluggable audit backend interface.

    Implementations: LocalSQLiteBackend (default), SupabaseBackend.
    Selection via create_audit_backend() factory (audit/factory.py).

    All methods are async. log_event() MUST be called via asyncio.create_task()
    at all call sites — fire-and-forget, never propagate exceptions to the
    proxy request path.

    @runtime_checkable enables isinstance(obj, AuditBackend) structural checks
    for health monitoring and protocol validation.
    """

    async def log_event(self, event: AuditEvent) -> None:
        """Persist an audit event.

        Must NEVER raise — catch all exceptions internally.
        Call sites MUST use: asyncio.create_task(backend.log_event(event))
        """
        ...

    async def query_events(self, filters: EventFilters) -> list[AuditEvent]:
        """Query audit events for dashboard display.

        Returns results sorted by timestamp DESC (newest first).
        Applies all non-None EventFilters fields.
        """
        ...

    async def count_events(self, filters: EventFilters) -> int:
        """Count events matching filters.

        Uses SELECT COUNT(*) — never loads full rows.
        Applies same filters as query_events() excluding limit/offset.
        """
        ...

    async def health_check(self) -> bool:
        """Returns True if the backend is operational. Must not raise."""
        ...

    async def prune_old_events(self, retention_days: int = 90) -> int:
        """Delete events older than retention_days. Returns count of deleted rows."""
        ...

    async def close(self) -> None:
        """Clean up connections and resources. Called during graceful shutdown."""
        ...


# ─── NullAuditBackend ────────────────────────────────────────────────────────


class NullAuditBackend:
    """No-op AuditBackend — used during testing and as a fallback stub.

    All methods are async no-ops. Satisfies the AuditBackend protocol so
    downstream code can call audit_backend.log_event(...) without AttributeError.

    Not used in production after E-005-S-006 (real factory replaces this).
    Retained as a test utility.
    """

    async def log_event(self, event: AuditEvent) -> None:
        """No-op: log call discarded."""
        logger.debug("NullAuditBackend.log_event (stub)", scan_id=event.scan_id)

    async def query_events(self, filters: EventFilters) -> list[AuditEvent]:
        """No-op: always returns empty list."""
        return []

    async def count_events(self, filters: EventFilters) -> int:
        """No-op: always returns 0."""
        return 0

    async def health_check(self) -> bool:
        """Always healthy (stub)."""
        return True

    async def prune_old_events(self, retention_days: int = 90) -> int:
        """No-op: returns 0."""
        return 0

    async def close(self) -> None:
        """No-op."""
        logger.debug("NullAuditBackend.close (stub)")


# ─── Protocol compliance assertion ────────────────────────────────────────────
# NullAuditBackend must satisfy AuditBackend protocol.
# This assertion runs at import time — catches protocol drift immediately.
assert isinstance(NullAuditBackend(), AuditBackend), (
    "NullAuditBackend does not satisfy AuditBackend protocol — implementation error"
)
