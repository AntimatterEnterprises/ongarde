"""Unit tests for E-005-S-001: AuditEvent, EventFilters, AuditBackend Protocol.

Tests:
  - AuditEvent construction (ALLOW minimal, BLOCK full)
  - EventFilters defaults
  - AuditBackend isinstance check (structural duck-typing)
  - NullAuditBackend protocol compliance
  - Type alias correctness
  - schema_version default
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from app.audit.models import (
    AuditEvent,
)
from app.audit.protocol import AuditBackend, EventFilters, NullAuditBackend

# ─── AuditEvent Tests ─────────────────────────────────────────────────────────


class TestAuditEventAllow:
    """Tests for minimal ALLOW event construction (AC-E005-01 item 3)."""

    def test_allow_event_minimal_construction(self) -> None:
        """Minimal ALLOW event requires only 5 fields."""
        event = AuditEvent(
            scan_id="01HTEST00000000000000000000",
            timestamp=datetime.now(timezone.utc),
            user_id="user-001",
            action="ALLOW",
            direction="REQUEST",
        )
        assert isinstance(event, AuditEvent)

    def test_allow_event_schema_version_default(self) -> None:
        """schema_version defaults to 1 (AC-E005-01 item 3)."""
        event = AuditEvent(
            scan_id="01HTEST00000000000000000001",
            timestamp=datetime.now(timezone.utc),
            user_id="user-001",
            action="ALLOW",
            direction="REQUEST",
        )
        assert event.schema_version == 1

    def test_allow_event_optional_fields_default_none(self) -> None:
        """All optional fields default to None or False."""
        event = AuditEvent(
            scan_id="01HTEST00000000000000000002",
            timestamp=datetime.now(timezone.utc),
            user_id="user-001",
            action="ALLOW",
            direction="REQUEST",
        )
        assert event.rule_id is None
        assert event.risk_level is None
        assert event.redacted_excerpt is None
        assert event.test is False
        assert event.tokens_delivered is None
        assert event.truncated is False
        assert event.original_length is None
        assert event.advisory_presidio_entities is None
        assert event.allowlist_rule_id is None

    def test_allow_event_is_dataclass(self) -> None:
        """AuditEvent is a dataclass."""
        assert dataclasses.is_dataclass(AuditEvent)

    def test_allow_event_response_direction(self) -> None:
        """ALLOW event can have RESPONSE direction."""
        event = AuditEvent(
            scan_id="01HTEST00000000000000000003",
            timestamp=datetime.now(timezone.utc),
            user_id="user-001",
            action="ALLOW",
            direction="RESPONSE",
        )
        assert event.direction == "RESPONSE"


class TestAuditEventBlock:
    """Tests for BLOCK event construction with all conditional fields (AC-E005-01 item 4)."""

    def test_block_event_full_construction(self) -> None:
        """BLOCK event with all fields populated — all non-None."""
        event = AuditEvent(
            scan_id="01HBLOCK0000000000000000001",
            timestamp=datetime.now(timezone.utc),
            user_id="user-002",
            action="BLOCK",
            direction="REQUEST",
            rule_id="CREDENTIAL_DETECTED",
            risk_level="CRITICAL",
            redacted_excerpt="...context [CREDENTIAL] more context...",
            test=False,
            tokens_delivered=None,
            truncated=False,
            original_length=None,
            advisory_presidio_entities=["CREDIT_CARD"],
            allowlist_rule_id=None,
        )
        # AC-E005-01 item 4: rule_id, risk_level, redacted_excerpt all non-None
        assert event.rule_id is not None
        assert event.risk_level is not None
        assert event.redacted_excerpt is not None

        assert event.action == "BLOCK"
        assert event.rule_id == "CREDENTIAL_DETECTED"
        assert event.risk_level == "CRITICAL"
        assert event.advisory_presidio_entities == ["CREDIT_CARD"]

    def test_block_event_schema_version_is_1(self) -> None:
        """BLOCK event schema_version is always 1."""
        event = AuditEvent(
            scan_id="01HBLOCK0000000000000000002",
            timestamp=datetime.now(timezone.utc),
            user_id="user-002",
            action="BLOCK",
            direction="REQUEST",
            rule_id="CREDENTIAL_DETECTED",
            risk_level="HIGH",
            redacted_excerpt="[REDACTED]",
        )
        assert event.schema_version == 1

    def test_block_event_all_risk_levels(self) -> None:
        """All 4 risk levels are valid for BLOCK events."""
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            event = AuditEvent(
                scan_id=f"01HRISK{level[:2]}00000000000000000",
                timestamp=datetime.now(timezone.utc),
                user_id="user-003",
                action="BLOCK",
                direction="REQUEST",
                risk_level=level,  # type: ignore[arg-type]
            )
            assert event.risk_level == level


class TestAuditEventAllowSuppressed:
    """Tests for ALLOW_SUPPRESSED event construction."""

    def test_allow_suppressed_construction(self) -> None:
        """ALLOW_SUPPRESSED event with allowlist_rule_id set."""
        event = AuditEvent(
            scan_id="01HSUPPRESSED000000000000001",
            timestamp=datetime.now(timezone.utc),
            user_id="user-004",
            action="ALLOW_SUPPRESSED",
            direction="REQUEST",
            rule_id="CREDENTIAL_DETECTED",
            risk_level="HIGH",
            redacted_excerpt="[REDACTED]",
            allowlist_rule_id="allowlist-rule-001",
        )
        assert event.action == "ALLOW_SUPPRESSED"
        assert event.allowlist_rule_id == "allowlist-rule-001"


class TestAuditEventFieldCount:
    """Verify all 15 fields are present (AC-E005-01 item 1)."""

    def test_all_15_fields_present(self) -> None:
        """AuditEvent has exactly 15 declared fields."""
        field_names = {f.name for f in dataclasses.fields(AuditEvent)}
        expected = {
            "scan_id",
            "timestamp",
            "user_id",
            "action",
            "direction",
            "schema_version",
            "rule_id",
            "risk_level",
            "redacted_excerpt",
            "test",
            "tokens_delivered",
            "truncated",
            "original_length",
            "advisory_presidio_entities",
            "allowlist_rule_id",
        }
        assert field_names == expected


# ─── EventFilters Tests ────────────────────────────────────────────────────────


class TestEventFilters:
    """Tests for EventFilters defaults (AC-E005-01 item 2)."""

    def test_event_filters_defaults(self) -> None:
        """EventFilters() has correct defaults."""
        filters = EventFilters()
        assert filters.action is None
        assert filters.direction is None
        assert filters.user_id is None
        assert filters.since is None
        assert filters.until is None
        assert filters.limit == 50
        assert filters.offset == 0
        assert filters.test is None
        assert filters.risk_level is None
        assert filters.action_in is None

    def test_event_filters_all_10_fields_present(self) -> None:
        """EventFilters has exactly 10 declared fields (AC-E005-01 item 2)."""
        field_names = {f.name for f in dataclasses.fields(EventFilters)}
        expected = {
            "action",
            "direction",
            "user_id",
            "since",
            "until",
            "limit",
            "offset",
            "test",
            "risk_level",
            "action_in",
        }
        assert field_names == expected

    def test_event_filters_is_dataclass(self) -> None:
        """EventFilters is a dataclass."""
        assert dataclasses.is_dataclass(EventFilters)

    def test_event_filters_custom_values(self) -> None:
        """EventFilters accepts custom values for all fields."""
        now = datetime.now(timezone.utc)
        filters = EventFilters(
            action="BLOCK",
            direction="REQUEST",
            user_id="user-001",
            since=now,
            until=now,
            limit=100,
            offset=50,
            test=True,
            risk_level="CRITICAL",
            action_in=["BLOCK", "ALLOW_SUPPRESSED"],
        )
        assert filters.action == "BLOCK"
        assert filters.action_in == ["BLOCK", "ALLOW_SUPPRESSED"]
        assert filters.limit == 100
        assert filters.offset == 50


# ─── AuditBackend Protocol Tests ──────────────────────────────────────────────


class TestAuditBackendProtocol:
    """Tests for AuditBackend protocol duck-typing (AC-E005-06 items 5 & 6)."""

    def test_null_audit_backend_satisfies_protocol(self) -> None:
        """NullAuditBackend passes isinstance check against AuditBackend."""
        backend = NullAuditBackend()
        assert isinstance(backend, AuditBackend)

    def test_duck_typed_mock_satisfies_protocol(self) -> None:
        """Any class implementing all 6 async methods satisfies AuditBackend (AC-E005-06 item 6)."""

        class DuckTypedBackend:
            async def log_event(self, event: AuditEvent) -> None:
                pass

            async def query_events(self, filters: EventFilters) -> list[AuditEvent]:
                return []

            async def count_events(self, filters: EventFilters) -> int:
                return 0

            async def health_check(self) -> bool:
                return True

            async def prune_old_events(self, retention_days: int = 90) -> int:
                return 0

            async def close(self) -> None:
                pass

        duck = DuckTypedBackend()
        assert isinstance(duck, AuditBackend)

    def test_incomplete_class_does_not_satisfy_protocol(self) -> None:
        """A class missing methods does NOT satisfy AuditBackend protocol."""

        class IncompleteBackend:
            async def log_event(self, event: AuditEvent) -> None:
                pass
            # Missing: query_events, count_events, health_check, prune_old_events, close

        incomplete = IncompleteBackend()
        assert not isinstance(incomplete, AuditBackend)

    def test_protocol_has_6_async_methods(self) -> None:
        """AuditBackend protocol defines exactly 6 async methods (AC-E005-06 item 5)."""
        import inspect
        protocol_methods = [
            name for name, method in inspect.getmembers(AuditBackend, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        expected_methods = {
            "log_event",
            "query_events",
            "count_events",
            "health_check",
            "prune_old_events",
            "close",
        }
        assert set(protocol_methods) == expected_methods


class TestNullAuditBackend:
    """Tests for NullAuditBackend no-op behavior."""

    async def test_log_event_no_op(self) -> None:
        """log_event() is a no-op (returns None, no exception)."""
        backend = NullAuditBackend()
        event = AuditEvent(
            scan_id="01HNULL0000000000000000001",
            timestamp=datetime.now(timezone.utc),
            user_id="user-null",
            action="ALLOW",
            direction="REQUEST",
        )
        result = await backend.log_event(event)
        assert result is None

    async def test_query_events_returns_empty(self) -> None:
        """query_events() always returns empty list."""
        backend = NullAuditBackend()
        result = await backend.query_events(EventFilters())
        assert result == []

    async def test_count_events_returns_zero(self) -> None:
        """count_events() always returns 0."""
        backend = NullAuditBackend()
        result = await backend.count_events(EventFilters())
        assert result == 0

    async def test_health_check_returns_true(self) -> None:
        """health_check() always returns True."""
        backend = NullAuditBackend()
        result = await backend.health_check()
        assert result is True

    async def test_prune_old_events_returns_zero(self) -> None:
        """prune_old_events() always returns 0."""
        backend = NullAuditBackend()
        result = await backend.prune_old_events()
        assert result == 0

    async def test_close_no_op(self) -> None:
        """close() is a no-op (returns None, no exception)."""
        backend = NullAuditBackend()
        result = await backend.close()
        assert result is None


# ─── Public API Re-exports ────────────────────────────────────────────────────


class TestPackageExports:
    """Verify app.audit re-exports the full public API."""

    def test_audit_package_exports_audit_event(self) -> None:
        """app.audit.AuditEvent is importable."""
        from app.audit import AuditEvent as AE  # noqa: F401
        assert AE is AuditEvent

    def test_audit_package_exports_audit_backend(self) -> None:
        """app.audit.AuditBackend is importable."""
        from app.audit import AuditBackend as AB  # noqa: F401
        assert AB is AuditBackend

    def test_audit_package_exports_event_filters(self) -> None:
        """app.audit.EventFilters is importable."""
        from app.audit import EventFilters as EF  # noqa: F401
        assert EF is EventFilters

    def test_audit_package_exports_null_backend(self) -> None:
        """app.audit.NullAuditBackend is importable."""
        from app.audit import NullAuditBackend as NAB  # noqa: F401
        assert NAB is NullAuditBackend

    def test_audit_package_exports_type_aliases(self) -> None:
        """app.audit exports ActionType, DirectionType, RiskLevelType."""
        from app.audit import ActionType, DirectionType, RiskLevelType  # noqa: F401
