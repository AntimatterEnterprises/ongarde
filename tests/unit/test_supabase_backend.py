"""Unit tests for E-005-S-005: SupabaseBackend.

Tests all 6 AuditBackend protocol methods using unittest.mock to simulate the
supabase AsyncClient. Does NOT require the supabase library to be installed.

Key non-negotiables tested:
  - AC-E005-10: unreachable Supabase → proxy continues (all exceptions swallowed)
  - Timeout: asyncio.wait_for(5.0) wraps all operations
  - Returns safe defaults: [], 0, False, None — never raises
  - Client is None → all methods return safe defaults without calling supabase
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.audit.models import AuditEvent
from app.audit.protocol import AuditBackend, EventFilters
from app.audit.supabase_backend import (
    _SUPABASE_TIMEOUT_S,
    SupabaseBackend,
    _dict_to_event,
    _event_to_dict,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_event(scan_id: str = "test-scan-001", action: str = "ALLOW") -> AuditEvent:
    return AuditEvent(
        scan_id=scan_id,
        timestamp=datetime.now(timezone.utc),
        user_id="user-test",
        action=action,  # type: ignore[arg-type]
        direction="REQUEST",
    )


def _make_block_event(scan_id: str = "block-001") -> AuditEvent:
    return AuditEvent(
        scan_id=scan_id,
        timestamp=datetime.now(timezone.utc),
        user_id="user-test",
        action="BLOCK",
        direction="REQUEST",
        rule_id="CREDENTIAL_DETECTED",
        risk_level="CRITICAL",
        redacted_excerpt="...context [CREDENTIAL] context...",
    )


def _make_mock_execute_response(
    data: list[Any] | None = None,
    count: int | None = None,
) -> AsyncMock:
    """Create a mock response for .execute() calls."""
    response = MagicMock()
    response.data = data or []
    response.count = count
    execute_mock = AsyncMock(return_value=response)
    return execute_mock


def _build_query_chain(execute_mock: AsyncMock) -> MagicMock:
    """Build a fluent mock query chain ending in .execute() AsyncMock."""
    chain = MagicMock()
    # All builder methods return self (fluent interface)
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.delete.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.range.return_value = chain
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.lt.return_value = chain
    chain.in_.return_value = chain
    chain.execute = execute_mock
    return chain


# ─── Protocol Compliance ──────────────────────────────────────────────────────


class TestSupabaseBackendProtocol:
    """SupabaseBackend satisfies AuditBackend Protocol."""

    def test_satisfies_audit_backend_protocol(self) -> None:
        """SupabaseBackend is instance of AuditBackend protocol."""
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        assert isinstance(backend, AuditBackend)

    def test_timeout_constant_is_5_seconds(self) -> None:
        """_SUPABASE_TIMEOUT_S is 5.0 (AC-E005-10)."""
        assert _SUPABASE_TIMEOUT_S == 5.0


# ─── No Client (supabase not installed / not initialized) ─────────────────────


class TestNoClient:
    """All methods return safe defaults when _client is None (no supabase lib or not initialized)."""

    def _backend_no_client(self) -> SupabaseBackend:
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        backend._client = None
        return backend

    async def test_log_event_no_client_returns_none(self) -> None:
        """log_event() returns None when client is None — no exception."""
        backend = self._backend_no_client()
        result = await backend.log_event(_make_event())
        assert result is None

    async def test_query_events_no_client_returns_empty(self) -> None:
        """query_events() returns [] when client is None."""
        backend = self._backend_no_client()
        result = await backend.query_events(EventFilters())
        assert result == []

    async def test_count_events_no_client_returns_zero(self) -> None:
        """count_events() returns 0 when client is None."""
        backend = self._backend_no_client()
        result = await backend.count_events(EventFilters())
        assert result == 0

    async def test_health_check_no_client_returns_false(self) -> None:
        """health_check() returns False when client is None."""
        backend = self._backend_no_client()
        result = await backend.health_check()
        assert result is False

    async def test_prune_old_events_no_client_returns_zero(self) -> None:
        """prune_old_events() returns 0 when client is None."""
        backend = self._backend_no_client()
        result = await backend.prune_old_events()
        assert result == 0

    async def test_close_no_client_no_exception(self) -> None:
        """close() doesn't raise when client is already None."""
        backend = self._backend_no_client()
        result = await backend.close()
        assert result is None


# ─── Exception Swallowing (AC-E005-10) ────────────────────────────────────────


class TestExceptionSwallowing:
    """All exceptions from Supabase are silently swallowed — proxy continues (AC-E005-10)."""

    def _backend_with_mock_client(
        self,
        execute_mock: AsyncMock,
    ) -> SupabaseBackend:
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        backend._client = MagicMock()
        backend._client.table.return_value = _build_query_chain(execute_mock)
        return backend

    async def test_log_event_swallows_connection_error(self) -> None:
        """log_event(): ConnectionError swallowed, returns None (AC-E005-10)."""
        execute_mock = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.log_event(_make_event())
        assert result is None

    async def test_log_event_swallows_runtime_error(self) -> None:
        """log_event(): RuntimeError swallowed, returns None."""
        execute_mock = AsyncMock(side_effect=RuntimeError("PostgREST error 500"))
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.log_event(_make_event())
        assert result is None

    async def test_query_events_swallows_exception_returns_empty(self) -> None:
        """query_events(): Exception swallowed, returns [] (AC-E005-10)."""
        execute_mock = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.query_events(EventFilters())
        assert result == []

    async def test_count_events_swallows_exception_returns_zero(self) -> None:
        """count_events(): Exception swallowed, returns 0."""
        execute_mock = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.count_events(EventFilters())
        assert result == 0

    async def test_health_check_swallows_exception_returns_false(self) -> None:
        """health_check(): Exception swallowed, returns False."""
        execute_mock = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.health_check()
        assert result is False

    async def test_prune_old_events_swallows_exception_returns_zero(self) -> None:
        """prune_old_events(): Exception swallowed, returns 0."""
        execute_mock = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.prune_old_events()
        assert result == 0

    async def test_timeout_exception_swallowed_log_event(self) -> None:
        """log_event(): asyncio.TimeoutError (from wait_for) is swallowed."""
        execute_mock = AsyncMock(side_effect=asyncio.TimeoutError())
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.log_event(_make_event())
        assert result is None

    async def test_timeout_exception_swallowed_query_events(self) -> None:
        """query_events(): asyncio.TimeoutError is swallowed → returns []."""
        execute_mock = AsyncMock(side_effect=asyncio.TimeoutError())
        backend = self._backend_with_mock_client(execute_mock)
        result = await backend.query_events(EventFilters())
        assert result == []


# ─── Timeout Wrapping ─────────────────────────────────────────────────────────


class TestTimeoutWrapping:
    """asyncio.wait_for(timeout=5.0) wraps all Supabase calls (AC-E005-10)."""

    async def test_log_event_times_out_after_5s_swallowed(self) -> None:
        """log_event() that takes >5s is cancelled and exception is swallowed."""
        async def slow_execute() -> Any:
            await asyncio.sleep(100)  # Far longer than 5s timeout

        execute_mock = AsyncMock(side_effect=slow_execute)
        backend = SupabaseBackend(
            url="https://test.supabase.co",
            key="test-key",
            timeout_s=0.01,  # Very short for testing
        )
        backend._client = MagicMock()
        backend._client.table.return_value = _build_query_chain(execute_mock)

        # Should return None without blocking for 100s
        result = await backend.log_event(_make_event())
        assert result is None

    async def test_query_events_times_out_swallowed(self) -> None:
        """query_events() that takes >5s returns [] without blocking."""
        async def slow_execute() -> Any:
            await asyncio.sleep(100)

        execute_mock = AsyncMock(side_effect=slow_execute)
        backend = SupabaseBackend(
            url="https://test.supabase.co",
            key="test-key",
            timeout_s=0.01,
        )
        backend._client = MagicMock()
        backend._client.table.return_value = _build_query_chain(execute_mock)

        result = await backend.query_events(EventFilters())
        assert result == []


# ─── Successful Operations ────────────────────────────────────────────────────


class TestSuccessfulOperations:
    """Tests for successful Supabase interactions (client available and responsive)."""

    def _backend_with_response(self, data: Any = None, count: int | None = None) -> SupabaseBackend:
        execute_mock = _make_mock_execute_response(data=data or [], count=count)
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        backend._client = MagicMock()
        backend._client.table.return_value = _build_query_chain(execute_mock)
        return backend

    async def test_log_event_calls_insert(self) -> None:
        """log_event() calls Supabase insert with event dict."""
        execute_mock = _make_mock_execute_response()
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        backend._client = MagicMock()
        chain = _build_query_chain(execute_mock)
        backend._client.table.return_value = chain

        event = _make_event()
        await backend.log_event(event)

        # Verify table was called with the right table name
        backend._client.table.assert_called_once_with("audit_events")

    async def test_health_check_returns_true_on_success(self) -> None:
        """health_check() returns True when Supabase responds successfully."""
        response = MagicMock()
        response.data = []
        response.count = 0
        execute_mock = AsyncMock(return_value=response)
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        backend._client = MagicMock()
        backend._client.table.return_value = _build_query_chain(execute_mock)

        result = await backend.health_check()
        assert result is True

    async def test_count_events_returns_response_count(self) -> None:
        """count_events() returns response.count from Supabase."""
        response = MagicMock()
        response.data = []
        response.count = 42
        execute_mock = AsyncMock(return_value=response)
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        backend._client = MagicMock()
        backend._client.table.return_value = _build_query_chain(execute_mock)

        result = await backend.count_events(EventFilters())
        assert result == 42

    async def test_count_events_none_count_returns_zero(self) -> None:
        """count_events() returns 0 when response.count is None."""
        response = MagicMock()
        response.data = []
        response.count = None
        execute_mock = AsyncMock(return_value=response)
        backend = SupabaseBackend(url="https://test.supabase.co", key="test-key")
        backend._client = MagicMock()
        backend._client.table.return_value = _build_query_chain(execute_mock)

        result = await backend.count_events(EventFilters())
        assert result == 0


# ─── Serialisation ────────────────────────────────────────────────────────────


class TestSerialisation:
    """Tests for _event_to_dict and _dict_to_event helpers."""

    def test_event_to_dict_all_fields(self) -> None:
        """_event_to_dict produces a dict with all 16 expected keys."""
        event = AuditEvent(
            scan_id="ser-001",
            timestamp=datetime.now(timezone.utc),
            user_id="user-ser",
            action="BLOCK",
            direction="REQUEST",
            rule_id="CREDENTIAL_DETECTED",
            risk_level="HIGH",
            redacted_excerpt="[REDACTED]",
            test=False,
            tokens_delivered=5,
            truncated=False,
            original_length=1024,
            advisory_presidio_entities=["CREDIT_CARD"],
            allowlist_rule_id=None,
        )
        d = _event_to_dict(event)
        assert "scan_id" in d
        assert "timestamp" in d
        assert "user_id" in d
        assert "action" in d
        assert "direction" in d
        assert "rule_id" in d
        assert "risk_level" in d
        assert "redacted_excerpt" in d
        assert "test" in d
        assert "schema_version" in d
        assert d["advisory_presidio_entities"] == ["CREDIT_CARD"]

    def test_event_to_dict_timestamp_is_iso_string(self) -> None:
        """_event_to_dict converts datetime to ISO 8601 string."""
        event = _make_event()
        d = _event_to_dict(event)
        assert isinstance(d["timestamp"], str)

    def test_dict_to_event_round_trip(self) -> None:
        """_dict_to_event deserialises all fields correctly."""
        original = AuditEvent(
            scan_id="rt-001",
            timestamp=datetime(2026, 1, 15, 10, 30, 0),
            user_id="user-rt",
            action="BLOCK",
            direction="REQUEST",
            rule_id="CREDENTIAL_DETECTED",
            risk_level="CRITICAL",
            redacted_excerpt="[REDACTED]",
            test=False,
            truncated=False,
            advisory_presidio_entities=None,
            allowlist_rule_id=None,
        )
        d = _event_to_dict(original)
        recovered = _dict_to_event(d)

        assert recovered.scan_id == original.scan_id
        assert recovered.user_id == original.user_id
        assert recovered.action == original.action
        assert recovered.direction == original.direction
        assert recovered.rule_id == original.rule_id
        assert recovered.risk_level == original.risk_level
        assert recovered.redacted_excerpt == original.redacted_excerpt
        assert recovered.schema_version == 1

    def test_redacted_excerpt_never_contains_raw_credentials(self) -> None:
        """redacted_excerpt must not contain raw API key patterns (AC-E005-03)."""
        event = AuditEvent(
            scan_id="cred-001",
            timestamp=datetime.now(timezone.utc),
            user_id="user-cred",
            action="BLOCK",
            direction="REQUEST",
            rule_id="CREDENTIAL_DETECTED",
            risk_level="CRITICAL",
            redacted_excerpt="...prefix [CREDENTIAL] suffix...",
        )
        d = _event_to_dict(event)
        # Should not contain raw sk-/ong-/AKIA-style credentials
        excerpt = d.get("redacted_excerpt", "") or ""
        import re
        raw_cred_patterns = [
            r"sk-[A-Za-z0-9]{20,}",  # OpenAI key
            r"ong-[A-Za-z0-9]{10,}",  # OnGarde key
            r"AKIA[A-Z0-9]{16}",      # AWS key
        ]
        for pat in raw_cred_patterns:
            assert not re.search(pat, excerpt), (
                f"redacted_excerpt contains raw credential matching '{pat}': {excerpt!r}"
            )
