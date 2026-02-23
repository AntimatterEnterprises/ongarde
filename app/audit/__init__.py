"""OnGarde audit backend package.

Re-exports the public API for ergonomic imports:

    from app.audit import AuditEvent, AuditBackend, EventFilters

Layout (architecture.md §10.1):
    models.py        — AuditEvent + type aliases (ActionType, DirectionType, RiskLevelType)
    protocol.py      — AuditBackend Protocol + EventFilters + NullAuditBackend stub
    sqlite_backend.py — LocalSQLiteBackend (aiosqlite, WAL mode, PRAGMA version guard)
    supabase_backend.py — SupabaseBackend (async client, 5s timeout, exception swallowing)
    factory.py        — create_audit_backend() — backend selection by env vars
"""

from app.audit.models import (
    ActionType,
    AuditEvent,
    DirectionType,
    RiskLevelType,
)
from app.audit.protocol import (
    AuditBackend,
    EventFilters,
    NullAuditBackend,
)

__all__ = [
    # Type aliases
    "ActionType",
    "DirectionType",
    "RiskLevelType",
    # Dataclasses
    "AuditEvent",
    "EventFilters",
    # Protocol + implementations
    "AuditBackend",
    "NullAuditBackend",
]
