"""LocalSQLiteBackend — aiosqlite-based async audit backend.

Uses aiosqlite EXCLUSIVELY. The stdlib sqlite3 synchronous module is
PROHIBITED in app/audit/ (CI grep gate enforced in tests/unit/test_audit_lint.py).

Features (architecture.md §4.3):
  - WAL mode: PRAGMA journal_mode=WAL (mandatory — enables concurrent reads while writing)
  - Schema version guard: PRAGMA user_version=1 — RuntimeError on mismatch, refuse startup
  - Long-lived connection: opened in initialize(), closed in close()
  - Idempotent writes: INSERT OR IGNORE on scan_id UNIQUE constraint
  - All 6 AuditBackend protocol methods implemented
  - prune_old_events(): DELETE WHERE timestamp < cutoff (90-day retention)
  - run_retention_pruner(): background asyncio task, daily 3am UTC
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

import aiosqlite

from app.audit.models import AuditEvent
from app.audit.protocol import EventFilters
from app.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ─── Schema DDL ───────────────────────────────────────────────────────────────

_CREATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id                     TEXT NOT NULL UNIQUE,
    timestamp                   TEXT NOT NULL,
    user_id                     TEXT NOT NULL,
    action                      TEXT NOT NULL CHECK(action IN ('ALLOW', 'BLOCK', 'ALLOW_SUPPRESSED')),
    direction                   TEXT NOT NULL CHECK(direction IN ('REQUEST', 'RESPONSE')),
    rule_id                     TEXT,
    risk_level                  TEXT CHECK(risk_level IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW') OR risk_level IS NULL),
    redacted_excerpt            TEXT,
    test                        INTEGER NOT NULL DEFAULT 0,
    tokens_delivered            INTEGER,
    truncated                   INTEGER NOT NULL DEFAULT 0,
    original_length             INTEGER,
    advisory_presidio_entities  TEXT,
    allowlist_rule_id           TEXT,
    schema_version              INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit_events(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_audit_action
    ON audit_events(action);

CREATE INDEX IF NOT EXISTS idx_audit_user_id
    ON audit_events(user_id);

CREATE INDEX IF NOT EXISTS idx_audit_action_timestamp
    ON audit_events(action, timestamp DESC);
"""

_SCHEMA_VERSION = 1


# ─── Row deserialiser ─────────────────────────────────────────────────────────


def _row_to_audit_event(row: aiosqlite.Row) -> AuditEvent:
    """Convert an aiosqlite Row (dict-like) to an AuditEvent dataclass.

    Field mapping:
      timestamp   : ISO 8601 string  → datetime.fromisoformat()
      test        : int (0/1)        → bool()
      truncated   : int (0/1)        → bool()
      advisory_presidio_entities : JSON string → list[str] | None
    """
    advisory_raw: Optional[str] = row["advisory_presidio_entities"]
    advisory: Optional[list[str]] = json.loads(advisory_raw) if advisory_raw else None

    return AuditEvent(
        scan_id=row["scan_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        user_id=row["user_id"],
        action=row["action"],
        direction=row["direction"],
        schema_version=row["schema_version"],
        rule_id=row["rule_id"],
        risk_level=row["risk_level"],
        redacted_excerpt=row["redacted_excerpt"],
        test=bool(row["test"]),
        tokens_delivered=row["tokens_delivered"],
        truncated=bool(row["truncated"]),
        original_length=row["original_length"],
        advisory_presidio_entities=advisory,
        allowlist_rule_id=row["allowlist_rule_id"],
    )


# ─── LocalSQLiteBackend ───────────────────────────────────────────────────────


class LocalSQLiteBackend:
    """Async SQLite audit backend using aiosqlite exclusively.

    Architecture:
      - Single long-lived connection (open once in initialize(), close in close())
      - WAL mode: PRAGMA journal_mode=WAL — allows concurrent reads + writes
      - Schema version guard: RuntimeError on PRAGMA user_version != 0 or 1
      - INSERT OR IGNORE on scan_id — idempotent writes

    Default path: ~/.ongarde/audit.db
    Override via: ONGARDE_AUDIT_PATH environment variable
    Or pass db_path explicitly (used in tests).

    Usage:
        backend = LocalSQLiteBackend()
        await backend.initialize()   # raises RuntimeError on schema version mismatch
        asyncio.create_task(backend.log_event(event))  # fire-and-forget
        results = await backend.query_events(EventFilters(action="BLOCK"))
        await backend.close()
    """

    def __init__(self, db_path: str = "~/.ongarde/audit.db") -> None:
        self._db_path: str = os.path.expanduser(db_path)
        self._db: Optional[aiosqlite.Connection] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open the SQLite connection, enable WAL mode, and create/verify schema.

        Steps:
          1. Create parent directory if absent (os.makedirs)
          2. Open aiosqlite connection (long-lived)
          3. Set row_factory for dict-like row access
          4. Enable WAL: PRAGMA journal_mode=WAL
          5. Read PRAGMA user_version
             - 0: fresh DB → create schema, set user_version=1
             - 1: compatible schema → no-op (idempotent)
             - other: raises RuntimeError with migration hint

        Raises:
            RuntimeError: If PRAGMA user_version is neither 0 nor 1.
                          The FastAPI lifespan catches this and refuses startup.
        """
        # Ensure parent directory exists
        parent_dir = os.path.dirname(self._db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # Open long-lived connection
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode — must be before schema check
        # WAL allows concurrent reads while a write is in progress
        await self._db.execute("PRAGMA journal_mode=WAL;")

        # Read schema version
        cursor = await self._db.execute("PRAGMA user_version;")
        row = await cursor.fetchone()
        current_version: int = row[0] if row else 0

        if current_version == 0:
            # Fresh database — create full schema
            await self._db.executescript(_CREATE_SCHEMA_SQL)
            # executescript may not honour PRAGMA in all SQLite builds;
            # set user_version separately after the script.
            await self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION};")
            await self._db.commit()
            logger.info(
                "audit_db_schema_created",
                db_path=self._db_path,
                schema_version=_SCHEMA_VERSION,
            )
        elif current_version == _SCHEMA_VERSION:
            # Existing compatible schema — no action needed
            logger.info(
                "audit_db_schema_ok",
                db_path=self._db_path,
                schema_version=current_version,
            )
        else:
            # Incompatible schema — refuse startup
            await self._db.close()
            self._db = None
            raise RuntimeError(
                f"Unsupported audit database schema version: {current_version}. "
                "Please run 'ongarde db migrate' or delete ~/.ongarde/audit.db to reset."
            )

    async def close(self) -> None:
        """Close the aiosqlite connection gracefully."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.debug("audit_db_closed", db_path=self._db_path)

    # ── AuditBackend Protocol Methods ─────────────────────────────────────────

    async def log_event(self, event: AuditEvent) -> None:
        """Persist an audit event to SQLite.

        Fire-and-forget: called via asyncio.create_task() at all call sites.
        Uses INSERT OR IGNORE — idempotent on scan_id UNIQUE constraint.
        Catches ALL exceptions — NEVER re-raises to the proxy request path.

        datetime → ISO 8601 string
        bool → int (0/1)
        list[str] → JSON string (or NULL)
        """
        try:
            assert self._db is not None, "Database not initialized — call initialize() first"
            await self._db.execute(
                """INSERT OR IGNORE INTO audit_events
                   (scan_id, timestamp, user_id, action, direction,
                    rule_id, risk_level, redacted_excerpt, test,
                    tokens_delivered, truncated, original_length,
                    advisory_presidio_entities, allowlist_rule_id, schema_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.scan_id,
                    event.timestamp.isoformat(),
                    event.user_id,
                    event.action,
                    event.direction,
                    event.rule_id,
                    event.risk_level,
                    event.redacted_excerpt,
                    int(event.test),
                    event.tokens_delivered,
                    int(event.truncated),
                    event.original_length,
                    json.dumps(event.advisory_presidio_entities)
                    if event.advisory_presidio_entities is not None
                    else None,
                    event.allowlist_rule_id,
                    event.schema_version,
                ),
            )
            await self._db.commit()
        except Exception as exc:
            # NEVER re-raise — audit failure must not affect proxy operation
            logger.error(
                "audit_write_failed",
                scan_id=event.scan_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def query_events(self, filters: EventFilters) -> list[AuditEvent]:
        """Query audit events with full EventFilters support.

        Returns results sorted by timestamp DESC (newest first).
        action_in takes precedence over action when both set.
        All filter conditions use parameterized placeholders — no SQL injection risk.
        """
        assert self._db is not None, "Database not initialized"
        sql, params = _build_select_sql(filters, count_only=False)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_audit_event(row) for row in rows]

    async def count_events(self, filters: EventFilters) -> int:
        """Count events matching filters using SELECT COUNT(*).

        Never loads full rows. Applies same filters as query_events()
        but ignores limit/offset (count always counts all matching rows).
        """
        assert self._db is not None, "Database not initialized"
        sql, params = _build_select_sql(filters, count_only=True)
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def health_check(self) -> bool:
        """Returns True if the DB connection is alive and queryable."""
        try:
            assert self._db is not None
            await self._db.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def prune_old_events(self, retention_days: int = 90) -> int:
        """Delete events older than retention_days. Returns count of deleted rows.

        Boundary condition: events WHERE timestamp < cutoff are deleted.
        Events with timestamp == cutoff (exactly at the boundary) are KEPT.
        This matches AC-E005-08.

        Never called directly from the request path — invoked only by
        run_retention_pruner() background task via asyncio.create_task().
        """
        assert self._db is not None, "Database not initialized"
        cutoff = datetime.utcnow() - timedelta(days=retention_days)

        cursor = await self._db.execute(
            "DELETE FROM audit_events WHERE timestamp < ?",
            (cutoff.isoformat(),),
        )
        await self._db.commit()
        count: int = cursor.rowcount  # type: ignore[assignment]

        if count > 0:
            logger.info(
                "retention_prune_complete",
                deleted_count=count,
                retention_days=retention_days,
                cutoff=cutoff.isoformat(),
            )
        return count


# ─── SQL Builder Helper ───────────────────────────────────────────────────────


def _build_select_sql(
    filters: EventFilters, *, count_only: bool
) -> tuple[str, list[Any]]:
    """Build a parameterized SELECT query from EventFilters.

    Parameters:
        filters:    EventFilters instance with optional filter fields.
        count_only: If True, generates SELECT COUNT(*) without LIMIT/OFFSET.
                    If False, generates SELECT * with ORDER BY, LIMIT, OFFSET.

    Returns:
        (sql_string, params_list) — pass directly to aiosqlite.Connection.execute()

    All values use ? placeholders — no string interpolation in WHERE conditions.
    """
    if count_only:
        sql = "SELECT COUNT(*) FROM audit_events"
    else:
        sql = "SELECT * FROM audit_events"

    conditions: list[str] = []
    params: list[Any] = []

    # action_in takes precedence over action
    if filters.action_in is not None:
        placeholders = ",".join("?" for _ in filters.action_in)
        conditions.append(f"action IN ({placeholders})")
        params.extend(filters.action_in)
    elif filters.action is not None:
        conditions.append("action = ?")
        params.append(filters.action)

    if filters.direction is not None:
        conditions.append("direction = ?")
        params.append(filters.direction)

    if filters.user_id is not None:
        conditions.append("user_id = ?")
        params.append(filters.user_id)

    if filters.since is not None:
        conditions.append("timestamp >= ?")
        params.append(filters.since.isoformat())

    if filters.until is not None:
        conditions.append("timestamp <= ?")
        params.append(filters.until.isoformat())

    if filters.test is not None:
        conditions.append("test = ?")
        params.append(int(filters.test))

    if filters.risk_level is not None:
        conditions.append("risk_level = ?")
        params.append(filters.risk_level)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    if not count_only:
        sql += " ORDER BY timestamp DESC"
        sql += f" LIMIT {filters.limit} OFFSET {filters.offset}"

    return sql, params


# ─── Background Retention Pruner ──────────────────────────────────────────────


async def run_retention_pruner(
    backend: LocalSQLiteBackend,
    retention_days: int = 90,
) -> None:
    """Background asyncio task: run prune_old_events() daily at 3:00 AM UTC.

    Registered as asyncio.create_task() during FastAPI lifespan startup.
    NEVER blocks the event loop. NEVER propagates exceptions.
    Cancelled cleanly on shutdown via task.cancel().

    Retry policy:
      - asyncio.CancelledError → break loop cleanly (expected on shutdown)
      - Any other exception    → log ERROR, retry after 1 hour

    Timing:
      If server starts at 2:59 AM UTC, first prune runs in 1 minute.
      If server starts at 3:01 AM UTC, first prune runs in ~23h 59m.
    """
    while True:
        try:
            now = datetime.utcnow()
            # Schedule next run at 3:00 AM UTC
            next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if next_run <= now:
                # Past 3am today — schedule for tomorrow
                next_run += timedelta(days=1)
            sleep_seconds = (next_run - now).total_seconds()

            logger.info(
                "retention_pruner_scheduled",
                next_run_utc=next_run.isoformat(),
                sleep_seconds=sleep_seconds,
            )
            await asyncio.sleep(sleep_seconds)

            count = await backend.prune_old_events(retention_days=retention_days)
            logger.info(
                "retention_prune_complete",
                deleted_count=count,
                retention_days=retention_days,
            )

        except asyncio.CancelledError:
            logger.info("retention_pruner_cancelled")
            raise  # Re-raise for clean asyncio cancellation (task.cancelled() = True)

        except Exception as exc:
            logger.error(
                "retention_prune_error",
                error=str(exc),
                error_type=type(exc).__name__,
                retry_in_seconds=3600,
            )
            try:
                await asyncio.sleep(3600)  # Retry in 1 hour on unexpected error
            except asyncio.CancelledError:
                logger.info("retention_pruner_cancelled_during_retry_sleep")
                raise  # Propagate for clean asyncio cancellation
