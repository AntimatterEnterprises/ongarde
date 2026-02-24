"""SupabaseBackend — async Supabase audit backend.

All methods are async with a 5-second timeout (asyncio.wait_for).
ALL exceptions are silently swallowed — the proxy continues regardless.
The supabase library is an optional dependency — if not installed, all
methods log a warning and return empty/False/0 values.

Architecture:
  - Single AsyncClient (or Client with async support) initialized in initialize()
  - All queries time out at 5 seconds (AC-E005-10)
  - No exception ever propagates to the caller (AC-E005-10)
  - log_event() callers MUST use asyncio.create_task() — fire-and-forget

Install: pip install ongarde[full]  # includes supabase>=2.4.0

Environment:
  SUPABASE_URL  — required for SupabaseBackend selection in factory.py
  SUPABASE_KEY  — required (service role key, not anon key)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

from app.audit.models import AuditEvent
from app.audit.protocol import EventFilters
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Try to import the supabase library — it's an optional dependency.
# If unavailable, SupabaseBackend becomes a no-op backend that logs warnings.
try:
    from supabase import create_async_client  # type: ignore[import-untyped]
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False
    logger.warning(
        "supabase_library_not_installed",
        message="supabase package not installed — SupabaseBackend will be a no-op. "
                "Install with: pip install ongarde[full]",
    )

# ─── Timeout Constant ─────────────────────────────────────────────────────────

_SUPABASE_TIMEOUT_S = 5.0
"""All Supabase operations are wrapped in asyncio.wait_for(timeout=_SUPABASE_TIMEOUT_S)."""

_TABLE_NAME = "audit_events"
"""Supabase table name for audit events."""


# ─── SupabaseBackend ──────────────────────────────────────────────────────────


class SupabaseBackend:
    """Async Supabase audit backend.

    Implements the AuditBackend protocol against Supabase's PostgREST API.
    All operations have a 5-second timeout — unreachable Supabase never blocks
    the proxy request path.

    All exceptions are silently swallowed and logged at ERROR level.
    The proxy continues regardless of Supabase availability.

    Usage:
        backend = SupabaseBackend(url="https://...", key="service-role-key")
        await backend.initialize()
        asyncio.create_task(backend.log_event(event))  # fire-and-forget
        results = await backend.query_events(EventFilters(action="BLOCK"))
        await backend.close()

    Table schema (must be created in Supabase project):
        See architecture.md §4.3 for the CREATE TABLE SQL.
        All columns match LocalSQLiteBackend — behavioral equivalence required.
    """

    def __init__(
        self,
        url: str,
        key: str,
        table_name: str = _TABLE_NAME,
        timeout_s: float = _SUPABASE_TIMEOUT_S,
    ) -> None:
        self._url = url
        self._key = key
        self._table_name = table_name
        self._timeout_s = timeout_s
        self._client: Optional[Any] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the async Supabase client.

        If supabase library is not installed, logs warning and returns (no-op).
        If connection fails, swallows exception and logs error.
        """
        if not _SUPABASE_AVAILABLE:
            logger.warning(
                "supabase_backend_noop",
                reason="supabase library not installed",
            )
            return

        try:
            self._client = await asyncio.wait_for(
                create_async_client(self._url, self._key),
                timeout=self._timeout_s,
            )
            logger.info(
                "supabase_backend_initialized",
                table=self._table_name,
                timeout_s=self._timeout_s,
            )
        except Exception as exc:
            logger.error(
                "supabase_backend_init_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._client = None

    async def close(self) -> None:
        """Close the Supabase client (no-op — HTTP clients are stateless)."""
        self._client = None
        logger.debug("supabase_backend_closed")

    # ── AuditBackend Protocol Methods ─────────────────────────────────────────

    async def log_event(self, event: AuditEvent) -> None:
        """Persist an audit event to Supabase.

        Fire-and-forget: all exceptions are swallowed — proxy continues.
        Timeout: _SUPABASE_TIMEOUT_S seconds.
        On duplicate scan_id, behaviour depends on Supabase upsert settings;
        safe to ignore constraint errors.
        """
        if self._client is None:
            return

        try:
            payload = _event_to_dict(event)
            await asyncio.wait_for(
                self._client.table(self._table_name).insert(payload).execute(),
                timeout=self._timeout_s,
            )
        except Exception as exc:
            # Swallow ALL exceptions — SupabaseBackend failures NEVER affect the proxy
            logger.error(
                "supabase_log_event_failed",
                scan_id=event.scan_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def query_events(self, filters: EventFilters) -> list[AuditEvent]:
        """Query audit events from Supabase with EventFilters.

        Returns [] on timeout, error, or if client is not initialized.
        """
        if self._client is None:
            return []

        try:
            query = (
                self._client.table(self._table_name)
                .select("*")
                .order("timestamp", desc=True)
            )
            query = _apply_filters_to_query(query, filters)
            query = query.limit(filters.limit)
            if filters.offset:
                query = query.range(filters.offset, filters.offset + filters.limit - 1)

            response = await asyncio.wait_for(
                query.execute(),
                timeout=self._timeout_s,
            )

            if not response.data:
                return []

            return [_dict_to_event(row) for row in response.data]

        except Exception as exc:
            logger.error(
                "supabase_query_events_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

    async def count_events(self, filters: EventFilters) -> int:
        """Count events matching filters.

        Returns 0 on timeout, error, or if client is not initialized.
        Uses Supabase count() via select with count='exact'.
        """
        if self._client is None:
            return 0

        try:
            query = (
                self._client.table(self._table_name)
                .select("*", count="exact")
            )
            query = _apply_filters_to_query(query, filters)

            response = await asyncio.wait_for(
                query.execute(),
                timeout=self._timeout_s,
            )

            return response.count if response.count is not None else 0

        except Exception as exc:
            logger.error(
                "supabase_count_events_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return 0

    async def health_check(self) -> bool:
        """Returns True if Supabase is reachable within timeout.

        Runs a lightweight SELECT 1 via count query.
        Returns False on any error or if client is not initialized.
        """
        if self._client is None:
            return False

        try:
            response = await asyncio.wait_for(
                self._client.table(self._table_name)
                .select("scan_id", count="exact")
                .limit(1)
                .execute(),
                timeout=self._timeout_s,
            )
            return response is not None
        except Exception as exc:
            logger.error(
                "supabase_health_check_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    async def prune_old_events(self, retention_days: int = 90) -> int:
        """Delete events older than retention_days via Supabase RPC or DELETE.

        Returns 0 if Supabase DELETE doesn't return rowcount (behaviour differs
        from LocalSQLiteBackend — documented xfail in shared integration tests).
        All exceptions swallowed.
        """
        if self._client is None:
            return 0

        try:
            cutoff = datetime.utcnow() - timedelta(days=retention_days)
            # Supabase delete filter — all rows with timestamp < cutoff
            response = await asyncio.wait_for(
                self._client.table(self._table_name)
                .delete()
                .lt("timestamp", cutoff.isoformat())
                .execute(),
                timeout=self._timeout_s,
            )
            # Supabase may or may not return affected rowcount
            count = len(response.data) if response.data else 0
            if count > 0:
                logger.info(
                    "supabase_prune_complete",
                    deleted_count=count,
                    retention_days=retention_days,
                )
            return count

        except Exception as exc:
            logger.error(
                "supabase_prune_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return 0


# ─── Serialisation helpers ────────────────────────────────────────────────────


def _event_to_dict(event: AuditEvent) -> dict[str, Any]:
    """Serialise AuditEvent to a Supabase-compatible dict.

    datetime → ISO 8601 string
    bool → bool (Postgres understands Python bool)
    list → list (JSON column in Supabase)
    """

    advisory = event.advisory_presidio_entities
    return {
        "scan_id": event.scan_id,
        "timestamp": event.timestamp.isoformat(),
        "user_id": event.user_id,
        "action": event.action,
        "direction": event.direction,
        "rule_id": event.rule_id,
        "risk_level": event.risk_level,
        "redacted_excerpt": event.redacted_excerpt,
        "test": event.test,
        "tokens_delivered": event.tokens_delivered,
        "truncated": event.truncated,
        "original_length": event.original_length,
        "advisory_presidio_entities": advisory,
        "allowlist_rule_id": event.allowlist_rule_id,
        "schema_version": event.schema_version,
    }


def _dict_to_event(row: dict[str, Any]) -> AuditEvent:
    """Deserialise a Supabase row dict to an AuditEvent."""
    timestamp_raw = row.get("timestamp", "")
    if isinstance(timestamp_raw, str):
        timestamp = datetime.fromisoformat(timestamp_raw)
    elif isinstance(timestamp_raw, datetime):
        timestamp = timestamp_raw
    else:
        timestamp = datetime.utcnow()

    return AuditEvent(
        scan_id=row["scan_id"],
        timestamp=timestamp,
        user_id=row.get("user_id", ""),
        action=row.get("action", "ALLOW"),
        direction=row.get("direction", "REQUEST"),
        schema_version=row.get("schema_version", 1),
        rule_id=row.get("rule_id"),
        risk_level=row.get("risk_level"),
        redacted_excerpt=row.get("redacted_excerpt"),
        test=bool(row.get("test", False)),
        tokens_delivered=row.get("tokens_delivered"),
        truncated=bool(row.get("truncated", False)),
        original_length=row.get("original_length"),
        advisory_presidio_entities=row.get("advisory_presidio_entities"),
        allowlist_rule_id=row.get("allowlist_rule_id"),
    )


def _apply_filters_to_query(query: Any, filters: EventFilters) -> Any:
    """Apply EventFilters conditions to a Supabase query builder.

    action_in takes precedence over action when both set.
    All filter conditions use Supabase postgrest-py filter methods.
    """
    # action_in takes precedence
    if filters.action_in is not None:
        # PostgREST 'in' filter: cs = "in" (contains)
        query = query.in_("action", filters.action_in)
    elif filters.action is not None:
        query = query.eq("action", filters.action)

    if filters.direction is not None:
        query = query.eq("direction", filters.direction)

    if filters.user_id is not None:
        query = query.eq("user_id", filters.user_id)

    if filters.since is not None:
        query = query.gte("timestamp", filters.since.isoformat())

    if filters.until is not None:
        query = query.lte("timestamp", filters.until.isoformat())

    if filters.test is not None:
        query = query.eq("test", filters.test)

    if filters.risk_level is not None:
        query = query.eq("risk_level", filters.risk_level)

    return query
