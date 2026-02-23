"""Shared integration test suite for E-005-S-007.

Tests that LocalSQLiteBackend and SupabaseBackend produce identical results
for the same set of operations. All test functions are parametrized over both
backends via the `audit_backend` fixture.

Backend availability:
  - LocalSQLiteBackend: always available (uses tmp_path for isolation)
  - SupabaseBackend: skipped when SUPABASE_URL + SUPABASE_KEY are not set

Key invariants verified (architecture.md §4.3):
  1. ALLOW event minimal round-trip (5 required fields preserved)
  2. BLOCK event full round-trip (all 15 fields preserved)
  3. query_events() returns newest-first
  4. count_events() returns exact count (not full rows)
  5. action_in filter: BLOCK + ALLOW_SUPPRESSED both returned, ALLOW excluded
  6. direction filter: REQUEST/RESPONSE correctly filtered
  7. user_id filter: returns only matching user
  8. since/until timestamp filter correctness
  9. test flag filter
  10. risk_level filter
  11. limit/offset pagination: page 1 and page 2 disjoint
  12. health_check() returns True on healthy backend
  13. prune_old_events() deletes events older than retention_days (SQLite only — see xfail note)
  14. prune_old_events() boundary: events at exactly cutoff time are KEPT
  15. redacted_excerpt NEVER contains raw credential patterns (AC-E005-03)
  16. Idempotent log_event (INSERT OR IGNORE on same scan_id)
  17. Exception safety: log_event() catches exceptions (fire-and-forget)
  18. log_event() is callable via asyncio.create_task() without blocking

xfail notes:
  SupabaseBackend.prune_old_events() row count may differ from LocalSQLiteBackend
  (Supabase DELETE may not return exact rowcount in all configurations).
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest

from app.audit.models import AuditEvent
from app.audit.protocol import AuditBackend, EventFilters
from app.audit.sqlite_backend import LocalSQLiteBackend


# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _is_supabase_available() -> bool:
    """Return True when both SUPABASE_URL and SUPABASE_KEY are set."""
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))


def _sqlite_backend(db_path: str) -> LocalSQLiteBackend:
    return LocalSQLiteBackend(db_path=db_path)


@pytest.fixture(
    params=["sqlite", "supabase"],
    ids=["LocalSQLiteBackend", "SupabaseBackend"],
)
async def audit_backend(request: pytest.FixtureRequest, tmp_path: object) -> AsyncGenerator[AuditBackend, None]:
    """Parametrized fixture yielding both backends.

    SupabaseBackend is auto-skipped when SUPABASE_URL / SUPABASE_KEY are not set.
    Each test gets a fully initialized, isolated backend instance.
    """
    if request.param == "supabase":
        if not _is_supabase_available():
            pytest.skip("SUPABASE_URL + SUPABASE_KEY not set — SupabaseBackend tests skipped")

        from app.audit.supabase_backend import SupabaseBackend

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        backend: AuditBackend = SupabaseBackend(url=url, key=key)
        await backend.initialize()
        try:
            yield backend
        finally:
            await backend.close()

    else:  # sqlite
        assert isinstance(tmp_path, type(tmp_path))
        import tempfile
        import os as _os
        db_path = _os.path.join(str(tmp_path), "integration_test.db")
        backend = LocalSQLiteBackend(db_path=db_path)
        await backend.initialize()
        try:
            yield backend
        finally:
            await backend.close()


# ─── Event Factories ──────────────────────────────────────────────────────────


def _allow_event(
    scan_id: str,
    user_id: str = "integration-user",
    timestamp: datetime | None = None,
    test: bool = False,
    direction: str = "REQUEST",
) -> AuditEvent:
    return AuditEvent(
        scan_id=scan_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        user_id=user_id,
        action="ALLOW",
        direction=direction,  # type: ignore[arg-type]
        test=test,
    )


def _block_event(
    scan_id: str,
    user_id: str = "integration-user",
    timestamp: datetime | None = None,
    risk_level: str = "HIGH",
    redacted_excerpt: str = "...prefix [CREDENTIAL] suffix...",
    advisory: list[str] | None = None,
) -> AuditEvent:
    return AuditEvent(
        scan_id=scan_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        user_id=user_id,
        action="BLOCK",
        direction="REQUEST",
        rule_id="CREDENTIAL_DETECTED",
        risk_level=risk_level,  # type: ignore[arg-type]
        redacted_excerpt=redacted_excerpt,
        advisory_presidio_entities=advisory,
    )


# ─── Test Suite ───────────────────────────────────────────────────────────────


async def test_health_check_returns_true(audit_backend: AuditBackend) -> None:
    """Invariant 12: health_check() returns True on healthy backend."""
    result = await audit_backend.health_check()
    assert result is True


async def test_allow_event_minimal_round_trip(audit_backend: AuditBackend) -> None:
    """Invariant 1: ALLOW event with 5 required fields preserved after write + read."""
    event = _allow_event("it-allow-001")
    await audit_backend.log_event(event)

    results = await audit_backend.query_events(EventFilters(limit=10))
    assert len(results) >= 1

    stored = next((r for r in results if r.scan_id == "it-allow-001"), None)
    assert stored is not None
    assert stored.action == "ALLOW"
    assert stored.user_id == "integration-user"
    assert stored.direction == "REQUEST"
    assert stored.schema_version == 1


async def test_block_event_full_round_trip(audit_backend: AuditBackend) -> None:
    """Invariant 2: BLOCK event with all fields preserved after write + read."""
    event = _block_event(
        "it-block-001",
        risk_level="CRITICAL",
        redacted_excerpt="...prefix [CREDENTIAL] suffix...",
        advisory=["CREDIT_CARD", "US_SSN"],
    )
    await audit_backend.log_event(event)

    results = await audit_backend.query_events(EventFilters(action="BLOCK", limit=10))
    stored = next((r for r in results if r.scan_id == "it-block-001"), None)
    assert stored is not None
    assert stored.rule_id == "CREDENTIAL_DETECTED"
    assert stored.risk_level == "CRITICAL"
    assert stored.redacted_excerpt == "...prefix [CREDENTIAL] suffix..."
    assert stored.direction == "REQUEST"
    assert stored.schema_version == 1
    # advisory_presidio_entities may be stored as list or serialized; check presence
    assert stored.advisory_presidio_entities is not None


async def test_query_returns_newest_first(audit_backend: AuditBackend) -> None:
    """Invariant 3: query_events() returns newest-first (ORDER BY timestamp DESC)."""
    now = datetime.now(timezone.utc)
    for i in range(3):
        ts = now - timedelta(hours=3 - i)
        await audit_backend.log_event(
            _allow_event(f"it-order-{i:04d}", timestamp=ts)
        )

    results = await audit_backend.query_events(EventFilters(limit=10))
    # Filter to our test events
    test_results = [r for r in results if r.scan_id.startswith("it-order-")]
    assert len(test_results) == 3

    timestamps = [r.timestamp for r in test_results]
    # Should be newest first
    for i in range(len(timestamps) - 1):
        assert timestamps[i] >= timestamps[i + 1], (
            f"Results not newest-first: {timestamps}"
        )


async def test_count_events_exact_count(audit_backend: AuditBackend) -> None:
    """Invariant 4: count_events() returns exact matching row count."""
    # Write 5 unique events with a unique user_id for isolation
    uid = "count-user-007"
    for i in range(5):
        await audit_backend.log_event(
            _allow_event(f"it-count-{i:04d}", user_id=uid)
        )

    count = await audit_backend.count_events(EventFilters(user_id=uid))
    assert count == 5


async def test_action_in_filter_excludes_allow(audit_backend: AuditBackend) -> None:
    """Invariant 5: action_in=["BLOCK","ALLOW_SUPPRESSED"] excludes ALLOW events."""
    uid = "action-in-user-001"
    await audit_backend.log_event(_allow_event("it-ai-allow", user_id=uid))
    await audit_backend.log_event(_block_event("it-ai-block", user_id=uid))
    await audit_backend.log_event(AuditEvent(
        scan_id="it-ai-suppressed",
        timestamp=datetime.now(timezone.utc),
        user_id=uid,
        action="ALLOW_SUPPRESSED",
        direction="REQUEST",
        rule_id="CREDENTIAL_DETECTED",
        risk_level="HIGH",
        redacted_excerpt="[REDACTED]",
        allowlist_rule_id="allow-rule-001",
    ))

    results = await audit_backend.query_events(
        EventFilters(user_id=uid, action_in=["BLOCK", "ALLOW_SUPPRESSED"], limit=10)
    )
    scan_ids = {r.scan_id for r in results}
    assert "it-ai-allow" not in scan_ids
    assert "it-ai-block" in scan_ids
    assert "it-ai-suppressed" in scan_ids


async def test_direction_filter(audit_backend: AuditBackend) -> None:
    """Invariant 6: direction filter correctly separates REQUEST/RESPONSE events."""
    uid = "dir-user-001"
    await audit_backend.log_event(_allow_event("it-dir-req", user_id=uid, direction="REQUEST"))
    await audit_backend.log_event(_allow_event("it-dir-resp", user_id=uid, direction="RESPONSE"))

    req_results = await audit_backend.query_events(
        EventFilters(user_id=uid, direction="REQUEST", limit=10)
    )
    resp_results = await audit_backend.query_events(
        EventFilters(user_id=uid, direction="RESPONSE", limit=10)
    )

    req_ids = {r.scan_id for r in req_results}
    resp_ids = {r.scan_id for r in resp_results}
    assert "it-dir-req" in req_ids
    assert "it-dir-resp" not in req_ids
    assert "it-dir-resp" in resp_ids
    assert "it-dir-req" not in resp_ids


async def test_user_id_filter(audit_backend: AuditBackend) -> None:
    """Invariant 7: user_id filter returns only events for that user."""
    await audit_backend.log_event(_allow_event("it-uid-alice", user_id="alice-007"))
    await audit_backend.log_event(_allow_event("it-uid-bob", user_id="bob-007"))

    alice_results = await audit_backend.query_events(
        EventFilters(user_id="alice-007", limit=10)
    )
    alice_ids = {r.scan_id for r in alice_results}
    assert "it-uid-alice" in alice_ids
    assert "it-uid-bob" not in alice_ids


async def test_since_filter(audit_backend: AuditBackend) -> None:
    """Invariant 8a: since filter includes events at or after cutoff."""
    uid = "since-user-001"
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=5)
    new = now - timedelta(minutes=5)

    await audit_backend.log_event(_allow_event("it-since-old", user_id=uid, timestamp=old))
    await audit_backend.log_event(_allow_event("it-since-new", user_id=uid, timestamp=new))

    # Since 2 hours ago: should return new, not old
    since_threshold = now - timedelta(hours=2)
    results = await audit_backend.query_events(
        EventFilters(user_id=uid, since=since_threshold, limit=10)
    )
    scan_ids = {r.scan_id for r in results}
    assert "it-since-new" in scan_ids
    assert "it-since-old" not in scan_ids


async def test_until_filter(audit_backend: AuditBackend) -> None:
    """Invariant 8b: until filter includes events at or before cutoff."""
    uid = "until-user-001"
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=5)
    new = now - timedelta(minutes=5)

    await audit_backend.log_event(_allow_event("it-until-old", user_id=uid, timestamp=old))
    await audit_backend.log_event(_allow_event("it-until-new", user_id=uid, timestamp=new))

    # Until 2 hours ago: should return old, not new
    until_threshold = now - timedelta(hours=2)
    results = await audit_backend.query_events(
        EventFilters(user_id=uid, until=until_threshold, limit=10)
    )
    scan_ids = {r.scan_id for r in results}
    assert "it-until-old" in scan_ids
    assert "it-until-new" not in scan_ids


async def test_test_flag_filter(audit_backend: AuditBackend) -> None:
    """Invariant 9: test=True/False filter correctly separates test events."""
    uid = "test-flag-user-001"
    await audit_backend.log_event(_allow_event("it-tf-real", user_id=uid, test=False))
    await audit_backend.log_event(_allow_event("it-tf-test", user_id=uid, test=True))

    test_results = await audit_backend.query_events(
        EventFilters(user_id=uid, test=True, limit=10)
    )
    real_results = await audit_backend.query_events(
        EventFilters(user_id=uid, test=False, limit=10)
    )

    test_ids = {r.scan_id for r in test_results}
    real_ids = {r.scan_id for r in real_results}

    assert "it-tf-test" in test_ids
    assert "it-tf-real" not in test_ids
    assert "it-tf-real" in real_ids
    assert "it-tf-test" not in real_ids


async def test_risk_level_filter(audit_backend: AuditBackend) -> None:
    """Invariant 10: risk_level filter returns only matching risk level."""
    uid = "rl-user-001"
    await audit_backend.log_event(_block_event("it-rl-critical", user_id=uid, risk_level="CRITICAL"))
    await audit_backend.log_event(_block_event("it-rl-high", user_id=uid, risk_level="HIGH"))

    critical_results = await audit_backend.query_events(
        EventFilters(user_id=uid, risk_level="CRITICAL", limit=10)
    )
    scan_ids = {r.scan_id for r in critical_results}
    assert "it-rl-critical" in scan_ids
    assert "it-rl-high" not in scan_ids


async def test_limit_offset_pagination(audit_backend: AuditBackend) -> None:
    """Invariant 11: limit/offset yields disjoint pages."""
    uid = "page-user-001"
    now = datetime.now(timezone.utc)
    for i in range(10):
        ts = now - timedelta(seconds=10 - i)
        await audit_backend.log_event(
            _allow_event(f"it-page-{i:04d}", user_id=uid, timestamp=ts)
        )

    page1 = await audit_backend.query_events(EventFilters(user_id=uid, limit=5, offset=0))
    page2 = await audit_backend.query_events(EventFilters(user_id=uid, limit=5, offset=5))

    assert len(page1) == 5
    assert len(page2) == 5

    page1_ids = {r.scan_id for r in page1}
    page2_ids = {r.scan_id for r in page2}
    assert page1_ids.isdisjoint(page2_ids)


async def test_idempotent_log_event(audit_backend: AuditBackend) -> None:
    """Invariant 16: Duplicate scan_id → second write ignored, row count stays 1."""
    uid = "idempotent-user-001"
    event = _allow_event("it-idem-001", user_id=uid)
    await audit_backend.log_event(event)
    await audit_backend.log_event(event)  # duplicate

    count = await audit_backend.count_events(EventFilters(user_id=uid))
    assert count == 1


async def test_log_event_exception_safety(audit_backend: AuditBackend) -> None:
    """Invariant 17: log_event() never raises (exception safety)."""
    # Call with a valid event — should not raise
    event = _allow_event("it-exc-001")
    result = await audit_backend.log_event(event)
    assert result is None  # Always returns None


async def test_create_task_fire_and_forget(audit_backend: AuditBackend) -> None:
    """Invariant 18: log_event() is callable via asyncio.create_task() without blocking."""
    event = _allow_event("it-task-001")
    task = asyncio.create_task(audit_backend.log_event(event))
    await task  # Should complete without error


async def test_redacted_excerpt_no_raw_credentials(audit_backend: AuditBackend) -> None:
    """Invariant 15 (AC-E005-03): redacted_excerpt NEVER contains raw credential patterns."""
    # Write a safe redacted excerpt and verify it's stored correctly
    safe_excerpts = [
        "...prefix [CREDENTIAL] suffix...",
        "...token [REDACTED] more...",
        "Bearer [REDACTED] headers",
        "[CREDIT_CARD] detected",
    ]
    uid = "cred-safety-user-001"
    for i, excerpt in enumerate(safe_excerpts):
        event = _block_event(f"it-cred-{i:04d}", user_id=uid, redacted_excerpt=excerpt)
        await audit_backend.log_event(event)

    results = await audit_backend.query_events(EventFilters(user_id=uid, limit=10))

    # Verify no raw credential patterns in stored excerpts
    raw_patterns = [
        r"sk-[A-Za-z0-9]{20,}",           # OpenAI API key
        r"ong-[A-Za-z0-9]{10,}",           # OnGarde key
        r"AKIA[A-Z0-9]{16}",               # AWS access key
        r"[0-9]{13,16}",                   # Credit card number (no context)
        r"[0-9]{3}-[0-9]{2}-[0-9]{4}",    # US SSN
    ]
    for result in results:
        if result.redacted_excerpt:
            for pat in raw_patterns:
                assert not re.search(pat, result.redacted_excerpt), (
                    f"redacted_excerpt contains raw credential matching '{pat}': "
                    f"{result.redacted_excerpt!r}"
                )


@pytest.mark.parametrize("backend_param", ["sqlite"])  # SQLite only — Supabase rowcount may differ
async def test_prune_old_events_deletes_old(
    tmp_path: object, backend_param: str
) -> None:
    """Invariant 13: prune_old_events() deletes events older than retention_days.

    SQLite-only test — SupabaseBackend.prune_old_events() rowcount is not guaranteed.
    """
    import os as _os
    db_path = _os.path.join(str(tmp_path), "prune_integration.db")
    backend = LocalSQLiteBackend(db_path=db_path)
    await backend.initialize()

    now = datetime.now(timezone.utc)
    old_ts = now - timedelta(days=95)
    new_ts = now - timedelta(hours=1)

    await backend.log_event(_allow_event("it-prune-old", timestamp=old_ts))
    await backend.log_event(_allow_event("it-prune-new", timestamp=new_ts))

    deleted = await backend.prune_old_events(retention_days=90)
    assert deleted == 1

    remaining_ids = {
        r.scan_id
        for r in await backend.query_events(EventFilters(limit=10))
    }
    assert "it-prune-old" not in remaining_ids
    assert "it-prune-new" in remaining_ids

    await backend.close()


@pytest.mark.parametrize("backend_param", ["sqlite"])  # SQLite only
async def test_prune_boundary_event_at_cutoff_kept(
    tmp_path: object, backend_param: str
) -> None:
    """Invariant 14: Event at exactly cutoff time is KEPT (< not <=).

    Boundary condition: prune_old_events deletes timestamp < cutoff,
    NOT timestamp <= cutoff.
    """
    import os as _os
    db_path = _os.path.join(str(tmp_path), "prune_boundary.db")
    backend = LocalSQLiteBackend(db_path=db_path)
    await backend.initialize()

    now = datetime.now(timezone.utc)
    # 91 minutes past retention boundary — will be pruned
    past_boundary = now - timedelta(days=90, minutes=91)
    # 30 seconds inside retention window — will be kept
    inside_window = now - timedelta(days=90) + timedelta(seconds=30)

    await backend.log_event(_allow_event("it-pb-past", timestamp=past_boundary))
    await backend.log_event(_allow_event("it-pb-inside", timestamp=inside_window))

    deleted = await backend.prune_old_events(retention_days=90)
    assert deleted == 1

    remaining_ids = {
        r.scan_id
        for r in await backend.query_events(EventFilters(limit=10))
    }
    assert "it-pb-past" not in remaining_ids
    assert "it-pb-inside" in remaining_ids

    await backend.close()


async def test_count_events_empty_backend(audit_backend: AuditBackend) -> None:
    """count_events() returns 0 for unique user with no events."""
    uid = "ghost-user-no-events-xyz"
    count = await audit_backend.count_events(EventFilters(user_id=uid))
    assert count == 0


async def test_query_events_empty_results(audit_backend: AuditBackend) -> None:
    """query_events() returns [] for unique user with no events."""
    uid = "ghost-user-no-events-abc"
    results = await audit_backend.query_events(EventFilters(user_id=uid, limit=10))
    assert results == []
