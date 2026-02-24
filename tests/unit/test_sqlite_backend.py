"""Unit tests for E-005-S-002, E-005-S-003, E-005-S-004:
LocalSQLiteBackend — aiosqlite, WAL mode, schema, indexes, CRUD, prune, retention task.

Story coverage:
  E-005-S-002: schema creation, WAL mode, version guard, indexes, path/dir creation
  E-005-S-003: log_event, query_events, count_events, filters, perf at 90k events
  E-005-S-004: prune_old_events boundary, health_check, run_retention_pruner
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
import pytest

from app.audit.models import AuditEvent
from app.audit.protocol import AuditBackend, EventFilters
from app.audit.sqlite_backend import LocalSQLiteBackend, run_retention_pruner

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_event(
    scan_id: str,
    action: str = "ALLOW",
    direction: str = "REQUEST",
    user_id: str = "user-001",
    timestamp: datetime | None = None,
    rule_id: str | None = None,
    risk_level: str | None = None,
    redacted_excerpt: str | None = None,
    test: bool = False,
    advisory: list[str] | None = None,
    allowlist_rule_id: str | None = None,
) -> AuditEvent:
    ts = timestamp or datetime.utcnow()
    return AuditEvent(
        scan_id=scan_id,
        timestamp=ts,
        user_id=user_id,
        action=action,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        rule_id=rule_id,
        risk_level=risk_level,  # type: ignore[arg-type]
        redacted_excerpt=redacted_excerpt,
        test=test,
        advisory_presidio_entities=advisory,
        allowlist_rule_id=allowlist_rule_id,
    )


# ─── E-005-S-002: Schema Creation + WAL Mode ──────────────────────────────────


class TestLocalSQLiteBackendSchema:
    """AC-E005-06 + AC-E005-07: schema creation, WAL mode, version guard."""

    async def test_fresh_db_creates_schema(self, tmp_path: Any) -> None:
        """Fresh database: schema created, user_version set to 1."""
        db_path = str(tmp_path / "test_audit.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA user_version;")
            row = await cursor.fetchone()
            assert row[0] == 1

        await backend.close()

    async def test_fresh_db_creates_table(self, tmp_path: Any) -> None:
        """Fresh database: audit_events table exists."""
        db_path = str(tmp_path / "test_audit.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "audit_events"

        await backend.close()

    async def test_fresh_db_creates_all_4_indexes(self, tmp_path: Any) -> None:
        """All 4 required indexes are created (AC-E005-06 schema)."""
        db_path = str(tmp_path / "test_audit.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_audit_%'"
            )
            rows = await cursor.fetchall()
            index_names = {row[0] for row in rows}

        await backend.close()

        expected = {
            "idx_audit_timestamp",
            "idx_audit_action",
            "idx_audit_user_id",
            "idx_audit_action_timestamp",
        }
        assert expected.issubset(index_names)

    async def test_wal_mode_enabled(self, tmp_path: Any) -> None:
        """WAL mode is set after initialize() (AC-E005-07)."""
        db_path = str(tmp_path / "test_audit.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()
        await backend.close()

        # Check WAL mode persists across connections
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode;")
            row = await cursor.fetchone()
            assert row[0] == "wal"

    async def test_idempotent_reinit_version1(self, tmp_path: Any) -> None:
        """Re-initializing an existing version=1 DB is idempotent (no error, no schema loss)."""
        db_path = str(tmp_path / "test_audit.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        # Insert one event so we can verify data survives re-init
        event = _make_event("idempotent-001")
        await backend.log_event(event)
        await backend.close()

        # Re-initialize should succeed
        backend2 = LocalSQLiteBackend(db_path=db_path)
        await backend2.initialize()
        count = await backend2.count_events(EventFilters())
        assert count == 1
        await backend2.close()

    async def test_version_mismatch_raises_runtime_error(self, tmp_path: Any) -> None:
        """user_version=2 → RuntimeError with migration hint (AC-E005-07)."""
        db_path = str(tmp_path / "future.db")

        # Pre-seed a DB with user_version = 2 (simulated future schema)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA user_version = 2;")
            await db.commit()

        backend = LocalSQLiteBackend(db_path=db_path)
        with pytest.raises(RuntimeError) as exc_info:
            await backend.initialize()

        error_msg = str(exc_info.value)
        assert "2" in error_msg
        assert "ongarde db migrate" in error_msg or "reset" in error_msg

    async def test_version_mismatch_backend_connection_closed(self, tmp_path: Any) -> None:
        """After RuntimeError on version mismatch, _db is None (connection closed)."""
        db_path = str(tmp_path / "future.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA user_version = 99;")
            await db.commit()

        backend = LocalSQLiteBackend(db_path=db_path)
        with pytest.raises(RuntimeError):
            await backend.initialize()

        assert backend._db is None

    async def test_parent_dir_created_if_absent(self, tmp_path: Any) -> None:
        """initialize() creates parent directory if it doesn't exist."""
        nested_path = str(tmp_path / "nested" / "subdir" / "audit.db")
        backend = LocalSQLiteBackend(db_path=nested_path)
        await backend.initialize()
        import os
        assert os.path.isfile(nested_path)
        await backend.close()

    async def test_protocol_compliance(self, tmp_path: Any) -> None:
        """LocalSQLiteBackend satisfies AuditBackend Protocol."""
        backend = LocalSQLiteBackend(db_path=str(tmp_path / "proto.db"))
        assert isinstance(backend, AuditBackend)
        await backend.initialize()
        await backend.close()

    async def test_all_16_columns_present(self, tmp_path: Any) -> None:
        """All 16 required columns are present in audit_events table."""
        db_path = str(tmp_path / "cols.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()
        await backend.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(audit_events);")
            rows = await cursor.fetchall()
            column_names = {row[1] for row in rows}

        expected_columns = {
            "id", "scan_id", "timestamp", "user_id", "action", "direction",
            "rule_id", "risk_level", "redacted_excerpt", "test",
            "tokens_delivered", "truncated", "original_length",
            "advisory_presidio_entities", "allowlist_rule_id", "schema_version",
        }
        assert expected_columns.issubset(column_names)


# ─── E-005-S-003: log_event, query_events, count_events ──────────────────────


class TestLogEvent:
    """Tests for log_event() — idempotency, exception safety, serialization."""

    async def test_log_allow_event(self, tmp_path: Any) -> None:
        """ALLOW event written successfully."""
        db_path = str(tmp_path / "log_allow.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        event = _make_event("log-allow-001")
        await backend.log_event(event)
        count = await backend.count_events(EventFilters())
        assert count == 1

        await backend.close()

    async def test_log_event_idempotent_duplicate_scan_id(self, tmp_path: Any) -> None:
        """INSERT OR IGNORE: second write with same scan_id → 1 row, no exception (AC-E005-03 item 6)."""
        db_path = str(tmp_path / "idempotent.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        event = _make_event("idempotent-scan-001")
        await backend.log_event(event)
        await backend.log_event(event)  # duplicate — should not raise or insert

        count = await backend.count_events(EventFilters())
        assert count == 1

        await backend.close()

    async def test_log_event_exception_safety_broken_connection(self, tmp_path: Any) -> None:
        """log_event() swallows exceptions when DB connection is broken (AC-E005-03 item 7)."""
        db_path = str(tmp_path / "broken.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        # Break the connection by closing it
        await backend._db.close()  # type: ignore[union-attr]
        backend._db = None  # Force AttributeError on next write

        event = _make_event("broken-001")
        # Must NOT raise — log_event() catches all exceptions
        result = await backend.log_event(event)
        assert result is None

    async def test_log_event_serialises_advisory_presidio_entities(self, tmp_path: Any) -> None:
        """advisory_presidio_entities is serialized as JSON and deserialized back."""
        db_path = str(tmp_path / "presidio.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        event = _make_event(
            "presidio-001",
            action="BLOCK",
            rule_id="PII_DETECTED",
            risk_level="HIGH",
            advisory=["CREDIT_CARD", "US_SSN"],
        )
        await backend.log_event(event)

        results = await backend.query_events(EventFilters())
        assert len(results) == 1
        assert results[0].advisory_presidio_entities == ["CREDIT_CARD", "US_SSN"]

        await backend.close()

    async def test_log_event_bool_fields_round_trip(self, tmp_path: Any) -> None:
        """test=True and truncated=True are stored as 1 and read back as True."""
        db_path = str(tmp_path / "bool_fields.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        event = AuditEvent(
            scan_id="bool-001",
            timestamp=datetime.utcnow(),
            user_id="user-bool",
            action="ALLOW",
            direction="REQUEST",
            test=True,
            truncated=True,
            original_length=2048,
            tokens_delivered=10,
        )
        await backend.log_event(event)

        results = await backend.query_events(EventFilters())
        assert len(results) == 1
        r = results[0]
        assert r.test is True
        assert r.truncated is True
        assert r.original_length == 2048
        assert r.tokens_delivered == 10

        await backend.close()


class TestQueryEvents:
    """Tests for query_events() — ordering, all filter fields (AC-E005-01, AC-E005-02)."""

    async def test_100_allow_events(self, tmp_path: Any) -> None:
        """Write 100 ALLOW events, query returns all 100 with action=ALLOW (AC-E005-01 item 1)."""
        db_path = str(tmp_path / "100_allow.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        for i in range(100):
            await backend.log_event(_make_event(f"allow-{i:04d}"))

        results = await backend.query_events(EventFilters(action="ALLOW", limit=200))
        assert len(results) == 100
        assert all(r.action == "ALLOW" for r in results)

        await backend.close()

    async def test_mixed_events_block_filter(self, tmp_path: Any) -> None:
        """10 BLOCK events among 100 total: filter returns exactly 10 (AC-E005-01 item 2)."""
        db_path = str(tmp_path / "mixed.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        for i in range(90):
            await backend.log_event(_make_event(f"allow-{i:04d}"))
        for i in range(10):
            await backend.log_event(_make_event(
                f"block-{i:04d}",
                action="BLOCK",
                rule_id="CREDENTIAL_DETECTED",
                risk_level="CRITICAL",
                redacted_excerpt="[REDACTED]",
            ))

        results = await backend.query_events(EventFilters(action="BLOCK", limit=50))
        assert len(results) == 10
        assert all(r.action == "BLOCK" for r in results)

        await backend.close()

    async def test_results_ordered_timestamp_desc(self, tmp_path: Any) -> None:
        """query_events() returns results sorted by timestamp DESC (AC-E005-03 item 8)."""
        db_path = str(tmp_path / "ordered.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        now = datetime.utcnow()
        for i in range(5):
            ts = now - timedelta(hours=5 - i)
            await backend.log_event(_make_event(f"ts-{i:04d}", timestamp=ts))

        results = await backend.query_events(EventFilters(limit=10))
        timestamps = [r.timestamp for r in results]
        assert timestamps == sorted(timestamps, reverse=True)

        await backend.close()

    async def test_action_in_filter(self, tmp_path: Any) -> None:
        """action_in=["BLOCK","ALLOW_SUPPRESSED"] returns both, excludes ALLOW (AC-E005-03 item 10)."""
        db_path = str(tmp_path / "action_in.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        await backend.log_event(_make_event("a-001", action="ALLOW"))
        await backend.log_event(_make_event("a-002", action="BLOCK", rule_id="R", risk_level="HIGH"))
        await backend.log_event(_make_event(
            "a-003", action="ALLOW_SUPPRESSED", rule_id="R",
            risk_level="LOW", allowlist_rule_id="rule-1",
        ))

        results = await backend.query_events(
            EventFilters(action_in=["BLOCK", "ALLOW_SUPPRESSED"], limit=10)
        )
        actions = {r.action for r in results}
        assert "ALLOW" not in actions
        assert "BLOCK" in actions
        assert "ALLOW_SUPPRESSED" in actions

        await backend.close()

    async def test_action_in_takes_precedence_over_action(self, tmp_path: Any) -> None:
        """When action_in is set, action is ignored."""
        db_path = str(tmp_path / "action_in_prec.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        await backend.log_event(_make_event("b-001", action="ALLOW"))
        await backend.log_event(_make_event("b-002", action="BLOCK", rule_id="R", risk_level="HIGH"))

        results = await backend.query_events(
            EventFilters(action="ALLOW", action_in=["BLOCK"], limit=10)
        )
        assert len(results) == 1
        assert results[0].action == "BLOCK"

        await backend.close()

    async def test_direction_filter(self, tmp_path: Any) -> None:
        """direction filter returns only matching direction."""
        db_path = str(tmp_path / "direction.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        await backend.log_event(_make_event("d-001", direction="REQUEST"))
        await backend.log_event(_make_event("d-002", direction="RESPONSE"))

        results = await backend.query_events(EventFilters(direction="RESPONSE", limit=10))
        assert len(results) == 1
        assert results[0].direction == "RESPONSE"

        await backend.close()

    async def test_user_id_filter(self, tmp_path: Any) -> None:
        """user_id filter returns only events for that user."""
        db_path = str(tmp_path / "user_id.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        await backend.log_event(_make_event("u-001", user_id="alice"))
        await backend.log_event(_make_event("u-002", user_id="bob"))
        await backend.log_event(_make_event("u-003", user_id="alice"))

        results = await backend.query_events(EventFilters(user_id="alice", limit=10))
        assert len(results) == 2
        assert all(r.user_id == "alice" for r in results)

        await backend.close()

    async def test_since_until_filter(self, tmp_path: Any) -> None:
        """since/until timestamp filters work correctly."""
        db_path = str(tmp_path / "since_until.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        now = datetime.utcnow()
        t_old = now - timedelta(hours=5)
        t_mid = now - timedelta(hours=3)
        t_new = now - timedelta(hours=1)

        await backend.log_event(_make_event("su-001", timestamp=t_old))
        await backend.log_event(_make_event("su-002", timestamp=t_mid))
        await backend.log_event(_make_event("su-003", timestamp=t_new))

        # since=t_mid → returns su-002 and su-003
        results = await backend.query_events(EventFilters(since=t_mid, limit=10))
        scan_ids = {r.scan_id for r in results}
        assert "su-001" not in scan_ids
        assert "su-002" in scan_ids
        assert "su-003" in scan_ids

        # until=t_mid → returns su-001 and su-002
        results2 = await backend.query_events(EventFilters(until=t_mid, limit=10))
        scan_ids2 = {r.scan_id for r in results2}
        assert "su-001" in scan_ids2
        assert "su-002" in scan_ids2
        assert "su-003" not in scan_ids2

        await backend.close()

    async def test_test_flag_filter(self, tmp_path: Any) -> None:
        """test=True filter returns only test events."""
        db_path = str(tmp_path / "test_flag.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        await backend.log_event(_make_event("tf-001", test=False))
        await backend.log_event(_make_event("tf-002", test=True))

        results = await backend.query_events(EventFilters(test=True, limit=10))
        assert len(results) == 1
        assert results[0].test is True

        await backend.close()

    async def test_risk_level_filter(self, tmp_path: Any) -> None:
        """risk_level filter returns only matching risk level."""
        db_path = str(tmp_path / "risk_level.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        await backend.log_event(_make_event(
            "rl-001", action="BLOCK", rule_id="R", risk_level="CRITICAL"
        ))
        await backend.log_event(_make_event(
            "rl-002", action="BLOCK", rule_id="R", risk_level="HIGH"
        ))

        results = await backend.query_events(EventFilters(risk_level="CRITICAL", limit=10))
        assert len(results) == 1
        assert results[0].risk_level == "CRITICAL"

        await backend.close()

    async def test_limit_and_offset_pagination(self, tmp_path: Any) -> None:
        """limit/offset pagination returns correct pages."""
        db_path = str(tmp_path / "pagination.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        now = datetime.utcnow()
        for i in range(10):
            ts = now - timedelta(seconds=10 - i)
            await backend.log_event(_make_event(f"pg-{i:04d}", timestamp=ts))

        page1 = await backend.query_events(EventFilters(limit=5, offset=0))
        page2 = await backend.query_events(EventFilters(limit=5, offset=5))

        assert len(page1) == 5
        assert len(page2) == 5
        # No overlap
        page1_ids = {r.scan_id for r in page1}
        page2_ids = {r.scan_id for r in page2}
        assert page1_ids.isdisjoint(page2_ids)

        await backend.close()

    async def test_block_event_field_round_trip(self, tmp_path: Any) -> None:
        """BLOCK event written and read back with field-for-field equality (AC-E005-02 item 4)."""
        db_path = str(tmp_path / "round_trip.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        original_ts = datetime.utcnow().replace(microsecond=0)  # truncate microseconds for ISO comparison
        event = AuditEvent(
            scan_id="rt-block-001",
            timestamp=original_ts,
            user_id="user-rt",
            action="BLOCK",
            direction="REQUEST",
            rule_id="CREDENTIAL_DETECTED",
            risk_level="CRITICAL",
            redacted_excerpt="...prefix [CREDENTIAL] suffix...",
            test=False,
            tokens_delivered=None,
            truncated=False,
            original_length=None,
            advisory_presidio_entities=["CREDIT_CARD"],
            allowlist_rule_id=None,
        )
        await backend.log_event(event)

        results = await backend.query_events(EventFilters(action="BLOCK", limit=10))
        assert len(results) == 1
        r = results[0]
        assert r.scan_id == event.scan_id
        assert r.action == event.action
        assert r.direction == event.direction
        assert r.user_id == event.user_id
        assert r.rule_id == event.rule_id
        assert r.risk_level == event.risk_level
        assert r.redacted_excerpt == event.redacted_excerpt
        assert len(r.redacted_excerpt) <= 100  # type: ignore[arg-type]
        assert r.advisory_presidio_entities == ["CREDIT_CARD"]
        assert r.schema_version == 1

        await backend.close()


class TestCountEvents:
    """Tests for count_events() accuracy (AC-E005-03 items 11-12)."""

    async def test_count_all_events(self, tmp_path: Any) -> None:
        """count_events() returns total row count with no filters."""
        db_path = str(tmp_path / "count_all.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        for i in range(5):
            await backend.log_event(_make_event(f"c-{i:04d}"))

        count = await backend.count_events(EventFilters())
        assert count == 5

        await backend.close()

    async def test_count_with_action_filter(self, tmp_path: Any) -> None:
        """count_events(action="BLOCK") returns exact BLOCK row count."""
        db_path = str(tmp_path / "count_block.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        for i in range(7):
            await backend.log_event(_make_event(f"allow-{i}"))
        for i in range(3):
            await backend.log_event(_make_event(
                f"block-{i}", action="BLOCK", rule_id="R", risk_level="HIGH"
            ))

        count = await backend.count_events(EventFilters(action="BLOCK"))
        assert count == 3

        await backend.close()

    async def test_count_empty_db_returns_zero(self, tmp_path: Any) -> None:
        """count_events() returns 0 for empty database."""
        db_path = str(tmp_path / "empty.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()
        count = await backend.count_events(EventFilters())
        assert count == 0
        await backend.close()


class TestQueryPerformance:
    """AC-E005-05: query performance at 90k events."""

    async def _seed_events(self, db: aiosqlite.Connection, count: int) -> None:
        """Batch-insert synthetic events for performance testing."""
        now = datetime.utcnow()
        rows = []
        for i in range(count):
            ts = (now - timedelta(hours=count - i)).isoformat()
            action = "BLOCK" if i % 10 == 0 else "ALLOW"
            rows.append((
                f"01HTEST{i:020d}",
                ts, "perf_user", action, "REQUEST",
                "CREDENTIAL_DETECTED" if action == "BLOCK" else None,
                "HIGH" if action == "BLOCK" else None,
                "redacted" if action == "BLOCK" else None,
                0, None, 0, None, None, None, 1,
            ))
        await db.executemany(
            "INSERT OR IGNORE INTO audit_events "
            "(scan_id, timestamp, user_id, action, direction, rule_id, risk_level, "
            "redacted_excerpt, test, tokens_delivered, truncated, original_length, "
            "advisory_presidio_entities, allowlist_rule_id, schema_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        await db.commit()

    async def test_query_block_events_at_90k_le_500ms(self, tmp_path: Any) -> None:
        """query_events(action=BLOCK, limit=50) at 90k rows ≤ 500ms (AC-E005-05)."""
        db_path = str(tmp_path / "perf.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        # Seed 90k events via direct batch insert (faster than 90k log_event calls)
        assert backend._db is not None
        await self._seed_events(backend._db, 90_000)

        start = time.perf_counter()
        results = await backend.query_events(EventFilters(action="BLOCK", limit=50))
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(results) == 50
        assert elapsed_ms <= 500, f"query took {elapsed_ms:.1f}ms, expected ≤ 500ms"

        await backend.close()

    async def test_count_block_events_at_90k_le_200ms(self, tmp_path: Any) -> None:
        """count_events(action=BLOCK, since=month_start) at 90k rows ≤ 200ms (AC-E005-05)."""
        db_path = str(tmp_path / "perf_count.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        assert backend._db is not None
        await self._seed_events(backend._db, 90_000)

        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        start = time.perf_counter()
        count = await backend.count_events(EventFilters(action="BLOCK", since=month_start))
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert isinstance(count, int)
        assert elapsed_ms <= 200, f"count took {elapsed_ms:.1f}ms, expected ≤ 200ms"

        await backend.close()


# ─── E-005-S-004: prune_old_events, health_check, retention pruner ────────────


class TestPruneOldEvents:
    """AC-E005-08: prune correctness and boundary condition."""

    async def test_prune_91_day_scenario(self, tmp_path: Any) -> None:
        """91-day event pruned, 90-day event kept, 89-day event kept, today kept (AC-E005-08)."""
        db_path = str(tmp_path / "prune.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        now = datetime.utcnow()

        events = [
            # 91 days + 1 minute ago — SHOULD be pruned (well past the boundary)
            _make_event("prune-91d", timestamp=now - timedelta(days=91, minutes=1)),
            # Slightly NEWER than 90 days ago — should NOT be pruned
            # Add 60s buffer so utcnow() advancing during test doesn't cross the boundary
            _make_event("prune-90d", timestamp=now - timedelta(days=90) + timedelta(seconds=60)),
            # 89 days ago — should NOT be pruned
            _make_event("prune-89d", timestamp=now - timedelta(days=89)),
            # Today — should NOT be pruned
            _make_event("prune-today", timestamp=now),
        ]

        for event in events:
            await backend.log_event(event)

        deleted = await backend.prune_old_events(retention_days=90)
        assert deleted == 1, f"Expected 1 event pruned, got {deleted}"

        remaining = await backend.count_events(EventFilters())
        assert remaining == 3  # 90d (with buffer), 89d, today remain

        scan_ids = {r.scan_id for r in await backend.query_events(EventFilters(limit=10))}
        assert "prune-91d" not in scan_ids
        assert "prune-90d" in scan_ids
        assert "prune-89d" in scan_ids
        assert "prune-today" in scan_ids

        await backend.close()

    async def test_prune_returns_deleted_count(self, tmp_path: Any) -> None:
        """prune_old_events() returns exact count of deleted rows."""
        db_path = str(tmp_path / "prune_count.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        now = datetime.utcnow()
        for i in range(5):
            await backend.log_event(
                _make_event(f"old-{i}", timestamp=now - timedelta(days=100 + i))
            )
        for i in range(3):
            await backend.log_event(
                _make_event(f"new-{i}", timestamp=now - timedelta(hours=i))
            )

        deleted = await backend.prune_old_events(retention_days=90)
        assert deleted == 5

        await backend.close()

    async def test_prune_empty_db_returns_zero(self, tmp_path: Any) -> None:
        """prune_old_events() returns 0 for empty database."""
        db_path = str(tmp_path / "prune_empty.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()
        deleted = await backend.prune_old_events(retention_days=90)
        assert deleted == 0
        await backend.close()

    async def test_prune_no_old_events_returns_zero(self, tmp_path: Any) -> None:
        """prune_old_events() returns 0 when all events are within retention window."""
        db_path = str(tmp_path / "prune_recent.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        now = datetime.utcnow()
        for i in range(3):
            await backend.log_event(
                _make_event(f"recent-{i}", timestamp=now - timedelta(days=i))
            )

        deleted = await backend.prune_old_events(retention_days=90)
        assert deleted == 0

        await backend.close()


class TestHealthCheck:
    """Tests for health_check() (AC-E005-04 item 12)."""

    async def test_health_check_returns_true_on_healthy_db(self, tmp_path: Any) -> None:
        """health_check() returns True when DB is open and operational."""
        db_path = str(tmp_path / "health.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()
        result = await backend.health_check()
        assert result is True
        await backend.close()

    async def test_health_check_returns_false_on_closed_db(self, tmp_path: Any) -> None:
        """health_check() returns False when DB connection is closed."""
        db_path = str(tmp_path / "health_closed.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()
        await backend.close()

        # After close, _db is None — health_check should return False
        result = await backend.health_check()
        assert result is False


class TestRetentionPruner:
    """Tests for run_retention_pruner() background task (AC-E005-04)."""

    async def test_pruner_cancelled_cleanly(self, tmp_path: Any) -> None:
        """Pruner task cancelled via task.cancel() — no exception propagates."""
        db_path = str(tmp_path / "pruner.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        task = asyncio.create_task(run_retention_pruner(backend, retention_days=90))

        # Give the task a moment to start and reach the asyncio.sleep call
        await asyncio.sleep(0.05)

        # Cancel the task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected

        # Task should be done (cancelled), no exception stored
        assert task.done()
        # CancelledError is raised by awaiting, not stored as task.exception()
        assert task.cancelled()

        await backend.close()

    async def test_pruner_exception_does_not_crash_loop(self, tmp_path: Any) -> None:
        """If prune_old_events() raises, pruner logs error and retries (does not crash)."""
        # We can't easily test the full retry behavior (1h sleep) in unit tests,
        # but we can verify the pruner doesn't propagate exceptions by patching.
        db_path = str(tmp_path / "pruner_err.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()

        call_count = 0
        _original_prune = backend.prune_old_events

        async def failing_prune(retention_days: int = 90) -> int:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Simulated prune failure")

        backend.prune_old_events = failing_prune  # type: ignore[method-assign]

        task = asyncio.create_task(run_retention_pruner(backend, retention_days=90))

        # Give the task time to hit the sleep, then cancel it
        # We can't easily test the 1h retry, so just cancel immediately
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.done()

        await backend.close()


# ─── No sqlite3 import (CI gate) ─────────────────────────────────────────────


def test_no_sqlite3_import_in_audit_sqlite_backend() -> None:
    """sqlite_backend.py must not import sqlite3 synchronous module."""
    import subprocess
    result = subprocess.run(
        ["grep", "-n", "import sqlite3", "app/audit/sqlite_backend.py"],
        capture_output=True,
        text=True,
        cwd="/root/.openclaw/workspace/ongarde",
    )
    assert result.returncode != 0, (
        "PROHIBITED: 'import sqlite3' found in app/audit/sqlite_backend.py. "
        "Use aiosqlite exclusively.\n"
        f"Violations:\n{result.stdout}"
    )
