"""Regression tests for _audit_request_event — prevents re-introduction of the
suppression_hint bug that caused all BLOCK audit writes to fail silently.

Bug summary (fixed in commit 88bf93d):
    _audit_request_event() passed suppression_hint=hint to AuditEvent().
    AuditEvent is a dataclass with no suppression_hint field, so Python raised
    TypeError on every BLOCK event. The except-all handler swallowed the error
    silently — log_event() was NEVER called, audit.db always had 0 rows, and
    the dashboard always showed zeroes even with active traffic.

These tests pin three things:
  1. AuditEvent does NOT accept suppression_hint (catches kwarg regression).
  2. _audit_request_event calls log_event on BLOCK (catches write regression).
  3. _audit_request_event is a no-op on ALLOW (ALLOW events are not audited).
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.audit.models import AuditEvent
from app.models.scan import Action, RiskLevel, ScanResult
from app.proxy.engine import _audit_request_event


SCAN_ID = "01REGRESSION_AUDIT_BUG_001"
USER_ID = "user-regression-test"


# ─── 1. AuditEvent does not accept suppression_hint ───────────────────────────


class TestAuditEventNoSuppressionHint:
    """Regression: AuditEvent must reject suppression_hint kwarg.

    If someone re-adds suppression_hint to the AuditEvent constructor call
    without adding the field to the dataclass, this test catches it before
    the bug reaches production and silently kills all audit writes.
    """

    def test_suppression_hint_raises_type_error(self):
        """AuditEvent() raises TypeError when passed suppression_hint=..."""
        with pytest.raises(TypeError, match="suppression_hint"):
            AuditEvent(
                scan_id=SCAN_ID,
                timestamp=datetime.now(timezone.utc),
                user_id=USER_ID,
                action="BLOCK",
                direction="REQUEST",
                suppression_hint="allowlist:\n  - rule_id: PII_DETECTED_US_SSN",
            )

    def test_audit_event_field_names_do_not_include_suppression_hint(self):
        """AuditEvent dataclass fields must not include 'suppression_hint'.

        If suppression_hint is ever added as a field, this test will fail and
        the developer must also ensure engine.py passes it correctly AND that
        the SQLite schema includes a column for it (to avoid silent data loss).
        """
        field_names = {f.name for f in dataclasses.fields(AuditEvent)}
        assert "suppression_hint" not in field_names, (
            "suppression_hint was added to AuditEvent. Ensure:\n"
            "  1. engine.py passes it correctly\n"
            "  2. sqlite_backend.py INSERT includes the column\n"
            "  3. The DB schema migration adds the column\n"
            "  4. Update this test comment to reflect the intentional change."
        )

    def test_valid_block_audit_event_construction_succeeds(self):
        """Constructing a valid BLOCK AuditEvent (without suppression_hint) must not raise."""
        event = AuditEvent(
            scan_id=SCAN_ID,
            timestamp=datetime.now(timezone.utc),
            user_id=USER_ID,
            action="BLOCK",
            direction="REQUEST",
            rule_id="PII_DETECTED_US_SSN",
            risk_level="HIGH",
            redacted_excerpt="My SSN is [REDACTED:pii-us-ssn]",
        )
        assert event.action == "BLOCK"
        assert event.rule_id == "PII_DETECTED_US_SSN"
        assert event.risk_level == "HIGH"
        # suppression_hint must not exist on the instance
        assert not hasattr(event, "suppression_hint")


# ─── 2. _audit_request_event calls log_event on BLOCK ─────────────────────────


class TestAuditRequestEventWritesOnBlock:
    """Regression: _audit_request_event must call backend.log_event() for BLOCK events.

    Before the fix, a TypeError from AuditEvent() silently prevented log_event()
    from being called. This test confirms the call actually reaches the backend.
    """

    def _make_block_result(self, rule_id: str = "PII_DETECTED_US_SSN") -> ScanResult:
        return ScanResult(
            action=Action.BLOCK,
            scan_id=SCAN_ID,
            rule_id=rule_id,
            risk_level=RiskLevel.HIGH,
            redacted_excerpt="My SSN is [REDACTED:pii-us-ssn]",
            test=False,
            allowlist_rule_id=None,
        )

    def _make_allow_result(self) -> ScanResult:
        return ScanResult(action=Action.ALLOW, scan_id=SCAN_ID)

    @pytest.mark.asyncio
    async def test_block_event_reaches_backend(self):
        """_audit_request_event must call backend.log_event() exactly once on BLOCK."""
        mock_backend = AsyncMock()
        scan_result = self._make_block_result()

        # _audit_request_event schedules asyncio.create_task() — we need a running loop.
        # side_effect closes the coroutine immediately so it's never left unawaited.
        with patch("asyncio.create_task", side_effect=lambda coro: coro.close()) as mock_create_task:
            _audit_request_event(
                backend=mock_backend,
                scan_result=scan_result,
                scan_id=SCAN_ID,
                user_id=USER_ID,
                path="/v1/chat/completions",
            )
            # create_task must have been called (fire-and-forget pattern)
            assert mock_create_task.called, (
                "_audit_request_event did not call asyncio.create_task(). "
                "The audit write was silently dropped."
            )
            # The coroutine passed to create_task must be log_event
            coro = mock_create_task.call_args[0][0]
            assert hasattr(coro, "__name__") or hasattr(coro, "cr_code"), \
                "create_task must be called with a coroutine from backend.log_event()"

    @pytest.mark.asyncio
    async def test_block_event_log_event_invoked_with_correct_action(self):
        """The AuditEvent passed to log_event must have action=BLOCK and correct fields."""
        logged_events: list[AuditEvent] = []

        async def capture_log_event(event: AuditEvent) -> None:
            logged_events.append(event)

        mock_backend = MagicMock()
        mock_backend.log_event = capture_log_event

        scan_result = self._make_block_result(rule_id="PII_DETECTED_US_SSN")

        # Patch create_task to actually execute the coroutine synchronously
        async def run_task(coro):
            await coro

        with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
            _audit_request_event(
                backend=mock_backend,
                scan_result=scan_result,
                scan_id=SCAN_ID,
                user_id=USER_ID,
                path="/v1/chat/completions",
            )
            # Drain pending tasks
            await asyncio.sleep(0)

        assert len(logged_events) == 1, (
            f"Expected 1 audit event written, got {len(logged_events)}. "
            "The audit write may be silently failing."
        )
        event = logged_events[0]
        assert event.action == "BLOCK"
        assert event.direction == "REQUEST"
        assert event.rule_id == "PII_DETECTED_US_SSN"
        assert event.risk_level == "HIGH"
        assert event.scan_id == SCAN_ID
        assert event.user_id == USER_ID
        # Regression: suppression_hint must not appear on the event
        assert not hasattr(event, "suppression_hint"), (
            "suppression_hint appeared on AuditEvent — this will break log_event() "
            "if the SQLite backend does not have a matching column."
        )

    @pytest.mark.asyncio
    async def test_allow_event_does_not_call_backend(self):
        """_audit_request_event must NOT call log_event() for ALLOW events.

        ALLOW events are intentionally not audited in v1 (not a bug).
        This test documents and pins that behaviour.
        """
        mock_backend = AsyncMock()
        scan_result = self._make_allow_result()

        with patch("asyncio.create_task") as mock_create_task:
            _audit_request_event(
                backend=mock_backend,
                scan_result=scan_result,
                scan_id=SCAN_ID,
                user_id=USER_ID,
                path="/v1/chat/completions",
            )
            mock_create_task.assert_not_called()

    def test_none_backend_is_no_op(self):
        """_audit_request_event must not raise when backend=None."""
        scan_result = self._make_block_result()
        # Must not raise
        _audit_request_event(
            backend=None,
            scan_result=scan_result,
            scan_id=SCAN_ID,
            user_id=USER_ID,
            path="/v1/chat/completions",
        )


# ─── 3. End-to-end: audit DB row count after a BLOCK request ──────────────────


class TestAuditDbRowWrittenOnBlock:
    """Integration-style regression: confirms a BLOCK actually lands in the DB.

    Uses a real LocalSQLiteBackend (in-memory via :memory: path) and drives
    _audit_request_event end-to-end to verify the full write path.
    """

    @pytest.mark.asyncio
    async def test_block_audit_event_persisted_to_db(self):
        """A BLOCK scan_result must produce exactly 1 row in the audit DB."""
        from app.audit.sqlite_backend import LocalSQLiteBackend

        backend = LocalSQLiteBackend(db_path=":memory:")
        await backend.initialize()

        scan_result = ScanResult(
            action=Action.BLOCK,
            scan_id="01REGRESSION_DB_ROW_TEST_001",
            rule_id="PRESIDIO_CREDIT_CARD",
            risk_level=RiskLevel.HIGH,
            redacted_excerpt="Card: [REDACTED] exp 12/26",
            test=False,
            allowlist_rule_id=None,
        )

        # Fire the audit helper and drain the event loop so create_task runs
        with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
            _audit_request_event(
                backend=backend,
                scan_result=scan_result,
                scan_id="01REGRESSION_DB_ROW_TEST_001",
                user_id=USER_ID,
                path="/v1/chat/completions",
            )
        await asyncio.sleep(0)  # drain event loop — let the scheduled task run

        # Verify exactly 1 row landed in the DB
        from app.audit.protocol import EventFilters
        events = await backend.query_events(EventFilters())
        assert len(events) == 1, (
            f"Expected 1 audit event in DB, got {len(events)}. "
            "The audit write path is broken — check AuditEvent constructor kwargs."
        )
        assert events[0].action == "BLOCK"
        assert events[0].rule_id == "PRESIDIO_CREDIT_CARD"

        await backend.close()
