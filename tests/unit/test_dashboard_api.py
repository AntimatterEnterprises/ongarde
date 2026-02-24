"""Tests for OnGarde Dashboard API endpoints — E-008-S-008.

Tests all 5 dashboard data endpoints:
    GET /dashboard/api/status    — proxy status (Feature 1)
    GET /dashboard/api/counters  — request/block counts (Features 2+3)
    GET /dashboard/api/health    — scanner health (Feature 4)
    GET /dashboard/api/events    — recent blocked events (Feature 5)
    GET /dashboard/api/quota     — quota display (Feature 6)

Also tests:
    Static file serving: GET /dashboard → index.html
    HTML content: all 7 card section IDs present
    Security: no raw credentials in redacted_excerpt

TESTING STRATEGY:
    We use TestClient WITHOUT the context manager to avoid running the real
    ASGI lifespan (which starts Presidio, SQLite, calibration, etc.).
    Instead, we manually set app.state fields to controlled mock values.
    This keeps tests fast (< 1s each) and deterministic.

Story: E-008-S-001, E-008-S-008
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.audit.models import AuditEvent
from app.audit.protocol import NullAuditBackend

# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _mock_backend(events=None, count=0):
    """Create a fully-mocked AuditBackend."""
    mock = AsyncMock(spec=NullAuditBackend)
    mock.query_events = AsyncMock(return_value=events or [])
    mock.count_events = AsyncMock(return_value=count)
    return mock


def _mock_calibration():
    c = MagicMock()
    c.tier = "standard"
    c.sync_cap = 1000
    c.timeout_ms = 45.0
    c.timeout_s = 0.045
    c.calibration_ok = True
    c.fallback_reason = None
    c.measurements = {200: 10.0, 500: 20.0, 1000: 35.0}
    return c


def _mock_latency_tracker():
    """Return a real ScanLatencyTracker with a few pre-recorded samples."""
    from app.utils.health import ScanLatencyTracker
    tracker = ScanLatencyTracker()
    for ms in [20.0, 23.4, 30.0, 25.0, 28.0]:
        tracker.record(ms)
    return tracker


def _configured_client(
    ready=True,
    events=None,
    backend_count=0,
    supabase_url=None,
):
    """
    Create a TestClient for the dashboard with mocked app state.

    Uses TestClient WITHOUT the context manager to skip the ASGI lifespan.
    All required app.state fields are set manually.
    """
    from app.config import Config
    from app.main import create_app

    app = create_app()

    # Set state directly (bypasses lifespan)
    app.state.ready = ready
    app.state.config = Config.defaults()
    app.state.audit_backend = _mock_backend(events=events, count=backend_count)
    app.state.calibration = _mock_calibration()
    app.state.latency_tracker = _mock_latency_tracker()
    app.state.scan_pool = None  # no pool → scanner "unavailable"
    app.state.streaming_tracker = None

    if supabase_url is not None:
        os.environ["SUPABASE_URL"] = supabase_url
    else:
        os.environ.pop("SUPABASE_URL", None)

    # NO context manager — lifespan does NOT run, app.state stays as set above
    return TestClient(app, raise_server_exceptions=False)


def _make_event(
    action="BLOCK",
    rule_id="CREDENTIAL_DETECTED",
    risk_level="CRITICAL",
    direction="REQUEST",
    redacted_excerpt="sk-ant-api[CREDENTIAL_DETECTED]...",
    test=False,
) -> AuditEvent:
    return AuditEvent(
        scan_id="01HXQ7F9V8K5M3N2P0R4T6W8Y1",
        timestamp=datetime.now(timezone.utc),
        user_id="test-user",
        action=action,
        direction=direction,
        rule_id=rule_id,
        risk_level=risk_level,
        redacted_excerpt=redacted_excerpt,
        test=test,
    )


# ─── Dashboard Shell: Static File Serving ─────────────────────────────────────


class TestDashboardShell:
    """E-008-S-001: Static file serving at /dashboard."""

    def test_dashboard_returns_200(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert resp.status_code == 200

    def test_dashboard_slash_returns_200(self):
        client = _configured_client()
        resp = client.get("/dashboard/")
        assert resp.status_code == 200

    def test_dashboard_content_type_is_html(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert "text/html" in resp.headers.get("content-type", "")

    def test_dashboard_contains_ongarde_title(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"OnGarde" in resp.content

    def test_dashboard_has_status_card_id(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"status-card" in resp.content

    def test_dashboard_has_requests_card_id(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"requests-card" in resp.content

    def test_dashboard_has_blocks_card_id(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"blocks-card" in resp.content

    def test_dashboard_has_scanner_card_id(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"scanner-card" in resp.content

    def test_dashboard_has_quota_card_id(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"quota-card" in resp.content

    def test_dashboard_has_events_section_id(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"events-section" in resp.content

    def test_dashboard_has_api_key_card_id(self):
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"api-key-card" in resp.content

    def test_dashboard_has_aha_card_id(self):
        """Aha moment card must be present in HTML (hidden by default via JS)."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"aha-card" in resp.content

    def test_dashboard_has_css_bg_base_color(self):
        """Design system: --bg-base: #0f0f18 must be present in CSS."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"#0f0f18" in resp.content

    def test_dashboard_has_card_bg_color(self):
        """Design system: --bg-card: #1a1a2e must be present."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"#1a1a2e" in resp.content

    def test_dashboard_has_brand_purple(self):
        """Design system: --brand-purple: #667eea must be present."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"#667eea" in resp.content

    def test_dashboard_has_risk_critical_color(self):
        """Design system: CRITICAL risk color #e53e3e must be present."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"#e53e3e" in resp.content

    def test_dashboard_no_raw_credentials_in_html_skeleton(self):
        """Security: index.html skeleton must not contain raw API key patterns."""
        client = _configured_client()
        resp = client.get("/dashboard")
        content = resp.content
        assert b"sk-ant-api03-" not in content
        assert b"sk-proj-" not in content

    def test_dashboard_has_polling_interval_10000(self):
        """10-second polling (10000ms) must be present in JS."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"10000" in resp.content

    def test_dashboard_has_countdown_element(self):
        """'Refresh in Xs' countdown timer element must be present."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"countdown" in resp.content
        assert b"Refresh in" in resp.content

    def test_dashboard_aha_card_uses_localstorage(self):
        """Aha moment must use localStorage to track dismissal."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"aha_shown" in resp.content
        assert b"localStorage" in resp.content

    def test_dashboard_has_brand_gradient(self):
        """Brand gradient must be in CSS."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"brand-gradient" in resp.content

    def test_dashboard_has_inter_font(self):
        """Inter font must be referenced (Google Fonts)."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"Inter" in resp.content

    def test_dashboard_has_jetbrains_mono(self):
        """JetBrains Mono font must be referenced for code/excerpts."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"JetBrains" in resp.content

    def test_dashboard_has_en_garde_text(self):
        """'En Garde' fencing metaphor must appear in the dashboard HTML."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"En Garde" in resp.content

    def test_dashboard_has_suppression_hint_copy(self):
        """Copy suppression hint button must be present in detail drawer."""
        client = _configured_client()
        resp = client.get("/dashboard")
        assert b"suppression" in resp.content.lower()


# ─── GET /dashboard/api/status ────────────────────────────────────────────────


class TestStatusEndpoint:
    """E-008-S-002: Proxy status API."""

    def test_status_returns_200_when_ready(self):
        client = _configured_client(ready=True)
        resp = client.get("/dashboard/api/status")
        assert resp.status_code == 200

    def test_status_returns_503_when_not_ready(self):
        client = _configured_client(ready=False)
        resp = client.get("/dashboard/api/status")
        assert resp.status_code == 503

    def test_status_has_proxy_running(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/status")
        data = resp.json()
        assert data["proxy"] == "running"

    def test_status_has_scanner_field(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/status")
        data = resp.json()
        assert "scanner" in data
        assert data["scanner"] in ("healthy", "degraded", "error", "unavailable")

    def test_status_has_scanner_mode(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/status")
        data = resp.json()
        assert "scanner_mode" in data
        assert data["scanner_mode"] in ("full", "lite")

    def test_status_has_uptime_seconds(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/status")
        data = resp.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], int)
        assert data["uptime_seconds"] >= 0

    def test_status_has_port(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/status")
        data = resp.json()
        assert "port" in data
        assert isinstance(data["port"], int)


# ─── GET /dashboard/api/counters ──────────────────────────────────────────────


class TestCountersEndpoint:
    """E-008-S-003: Request and block counters."""

    def test_counters_returns_200(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/counters")
        assert resp.status_code == 200

    def test_counters_has_requests_field(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/counters")
        data = resp.json()
        assert "requests" in data
        assert "today" in data["requests"]
        assert "month" in data["requests"]

    def test_counters_has_blocks_field(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/counters")
        data = resp.json()
        assert "blocks" in data
        assert "today" in data["blocks"]
        assert "month" in data["blocks"]

    def test_counters_has_risk_breakdown(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/counters")
        data = resp.json()
        assert "risk_breakdown" in data
        bd = data["risk_breakdown"]
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            assert level in bd

    def test_counters_risk_breakdown_all_integers(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/counters")
        data = resp.json()
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            assert isinstance(data["risk_breakdown"][level], int)

    def test_counters_returns_zero_with_no_events(self):
        """With no events (count_events returns 0), all counts are zero."""
        client = _configured_client(backend_count=0)
        resp = client.get("/dashboard/api/counters")
        data = resp.json()
        assert isinstance(data["requests"]["today"], int)
        assert isinstance(data["blocks"]["today"], int)

    def test_counters_503_when_not_ready(self):
        client = _configured_client(ready=False)
        resp = client.get("/dashboard/api/counters")
        assert resp.status_code == 503


# ─── GET /dashboard/api/health ────────────────────────────────────────────────


class TestHealthEndpoint:
    """E-008-S-004: Scanner health card."""

    def test_health_returns_200(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        assert resp.status_code == 200

    def test_health_has_scanner_field(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        data = resp.json()
        assert "scanner" in data
        assert data["scanner"] in ("healthy", "degraded", "error")

    def test_health_has_scanner_mode(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        data = resp.json()
        assert "scanner_mode" in data
        assert data["scanner_mode"] in ("full", "lite")

    def test_health_has_latency_fields(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        data = resp.json()
        assert "avg_scan_ms" in data
        assert "p99_scan_ms" in data
        assert isinstance(data["avg_scan_ms"], (int, float))

    def test_health_has_queue_depth(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        data = resp.json()
        assert "queue_depth" in data
        assert isinstance(data["queue_depth"], int)

    def test_health_has_entity_set(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        data = resp.json()
        assert "entity_set" in data
        assert isinstance(data["entity_set"], list)

    def test_health_has_calibration_field(self):
        """Calibration field must be present (may be None or a dict)."""
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        data = resp.json()
        assert "calibration" in data

    def test_health_calibration_tier_when_present(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/health")
        data = resp.json()
        if data["calibration"]:
            assert "tier" in data["calibration"]
            assert data["calibration"]["tier"] in ("fast", "standard", "slow", "minimal")

    def test_health_503_when_not_ready(self):
        client = _configured_client(ready=False)
        resp = client.get("/dashboard/api/health")
        assert resp.status_code == 503


# ─── GET /dashboard/api/events ────────────────────────────────────────────────


class TestEventsEndpoint:
    """E-008-S-005: Recent blocked events."""

    def test_events_returns_200(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/events")
        assert resp.status_code == 200

    def test_events_has_events_list(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_events_has_total_field(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        assert "total" in data

    def test_events_single_block_event_has_all_fields(self):
        event = _make_event()
        from app.config import Config
        from app.main import create_app
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        backend = _mock_backend(events=[event])
        app.state.audit_backend = backend
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        assert len(data["events"]) == 1
        e = data["events"][0]
        for field in ("scan_id", "timestamp", "action", "rule_id", "risk_level",
                      "direction", "redacted_excerpt", "suppression_hint", "test"):
            assert field in e, f"Missing field: {field}"

    def test_events_suppression_hint_for_policy_block(self):
        event = _make_event(rule_id="CREDENTIAL_DETECTED")
        from app.config import Config
        from app.main import create_app
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        app.state.audit_backend = _mock_backend(events=[event])
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        hint = data["events"][0]["suppression_hint"]
        assert hint is not None
        assert "CREDENTIAL_DETECTED" in hint
        assert "rule_id" in hint

    def test_events_suppression_hint_null_for_scanner_error(self):
        event = _make_event(rule_id="SCANNER_ERROR")
        from app.config import Config
        from app.main import create_app
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        app.state.audit_backend = _mock_backend(events=[event])
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        hint = data["events"][0]["suppression_hint"]
        assert hint is None, f"Expected null hint for SCANNER_ERROR, got: {hint}"

    def test_events_suppression_hint_null_for_quota_exceeded(self):
        event = _make_event(rule_id="QUOTA_EXCEEDED")
        from app.config import Config
        from app.main import create_app
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        app.state.audit_backend = _mock_backend(events=[event])
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        assert data["events"][0]["suppression_hint"] is None

    def test_events_redacted_excerpt_capped_at_100_chars(self):
        long_excerpt = "A" * 200
        event = _make_event(redacted_excerpt=long_excerpt)
        from app.config import Config
        from app.main import create_app
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        app.state.audit_backend = _mock_backend(events=[event])
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        assert len(data["events"][0]["redacted_excerpt"]) <= 100

    def test_events_limit_caps_at_50(self):
        """limit=999 query param is capped at 50 in the backend call."""
        from app.config import Config
        from app.main import create_app
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        backend = _mock_backend(events=[])
        app.state.audit_backend = backend
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/dashboard/api/events?limit=999")
        filters = backend.query_events.call_args[0][0]
        assert filters.limit <= 50

    def test_events_include_suppressed_action_filter(self):
        """include_suppressed=true uses action_in=['BLOCK', 'ALLOW_SUPPRESSED']."""
        from app.config import Config
        from app.main import create_app
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        backend = _mock_backend(events=[])
        app.state.audit_backend = backend
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/dashboard/api/events?include_suppressed=true")
        filters = backend.query_events.call_args[0][0]
        assert "ALLOW_SUPPRESSED" in (filters.action_in or [])

    def test_events_503_when_not_ready(self):
        client = _configured_client(ready=False)
        resp = client.get("/dashboard/api/events")
        assert resp.status_code == 503

    def test_events_empty_list_on_no_events(self):
        client = _configured_client(events=[])
        resp = client.get("/dashboard/api/events")
        data = resp.json()
        assert data["events"] == []
        assert data["total"] == 0


# ─── GET /dashboard/api/quota ─────────────────────────────────────────────────


class TestQuotaEndpoint:
    """E-008-S-006: Quota display."""

    def test_quota_returns_200(self):
        client = _configured_client()
        resp = client.get("/dashboard/api/quota")
        assert resp.status_code == 200

    def test_quota_self_hosted_when_no_supabase(self):
        """Without SUPABASE_URL, quota is self-hosted with no limit."""
        os.environ.pop("SUPABASE_URL", None)
        client = _configured_client()
        resp = client.get("/dashboard/api/quota")
        data = resp.json()
        assert data["self_hosted"] is True
        assert data["limit"] is None

    def test_quota_self_hosted_has_audit_path(self):
        """Self-hosted response includes a non-null audit_path."""
        os.environ.pop("SUPABASE_URL", None)
        client = _configured_client()
        resp = client.get("/dashboard/api/quota")
        data = resp.json()
        assert "audit_path" in data
        assert data["audit_path"] is not None
        assert isinstance(data["audit_path"], str)

    def test_quota_self_hosted_has_used_count(self):
        """Self-hosted response includes used count from count_events."""
        from app.config import Config
        from app.main import create_app
        os.environ.pop("SUPABASE_URL", None)
        app = create_app()
        app.state.ready = True
        app.state.config = Config.defaults()
        backend = _mock_backend(count=42)
        app.state.audit_backend = backend
        app.state.calibration = _mock_calibration()
        app.state.latency_tracker = _mock_latency_tracker()
        app.state.scan_pool = None
        app.state.streaming_tracker = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/api/quota")
        data = resp.json()
        # count_events is mocked to return 42
        assert isinstance(data["used"], int)

    def test_quota_managed_when_supabase_set(self):
        """With SUPABASE_URL env var, quota is managed (self_hosted=False)."""
        client = _configured_client(supabase_url="https://test.supabase.co")
        resp = client.get("/dashboard/api/quota")
        data = resp.json()
        assert data["self_hosted"] is False
        assert data["limit"] is not None
        assert isinstance(data["limit"], int)
        os.environ.pop("SUPABASE_URL", None)  # cleanup

    def test_quota_503_when_not_ready(self):
        client = _configured_client(ready=False)
        resp = client.get("/dashboard/api/quota")
        assert resp.status_code == 503


# ─── _make_suppression_hint helper ───────────────────────────────────────────


class TestMakeSuppressionHint:
    """Unit tests for the suppression hint generator."""

    def test_hint_for_credential_detected(self):
        from app.dashboard.api import _make_suppression_hint
        hint = _make_suppression_hint("CREDENTIAL_DETECTED")
        assert hint is not None
        assert "CREDENTIAL_DETECTED" in hint
        assert "rule_id" in hint

    def test_hint_is_yaml_allowlist_snippet(self):
        from app.dashboard.api import _make_suppression_hint
        hint = _make_suppression_hint("DANGEROUS_COMMAND_DETECTED")
        assert "allowlist:" in hint
        assert "- rule_id:" in hint
        assert "note" in hint

    def test_hint_none_for_scanner_error(self):
        from app.dashboard.api import _make_suppression_hint
        assert _make_suppression_hint("SCANNER_ERROR") is None

    def test_hint_none_for_scanner_timeout(self):
        from app.dashboard.api import _make_suppression_hint
        assert _make_suppression_hint("SCANNER_TIMEOUT") is None

    def test_hint_none_for_quota_exceeded(self):
        from app.dashboard.api import _make_suppression_hint
        assert _make_suppression_hint("QUOTA_EXCEEDED") is None

    def test_hint_none_for_none_rule_id(self):
        from app.dashboard.api import _make_suppression_hint
        assert _make_suppression_hint(None) is None

    def test_hint_none_for_scanner_unavailable(self):
        from app.dashboard.api import _make_suppression_hint
        assert _make_suppression_hint("SCANNER_UNAVAILABLE") is None

    def test_hint_for_prompt_injection(self):
        from app.dashboard.api import _make_suppression_hint
        hint = _make_suppression_hint("PROMPT_INJECTION_DETECTED")
        assert hint is not None
        assert "PROMPT_INJECTION_DETECTED" in hint

    def test_hint_for_pii_email(self):
        from app.dashboard.api import _make_suppression_hint
        hint = _make_suppression_hint("PII_DETECTED_EMAIL_ADDRESS")
        assert hint is not None
        assert "PII_DETECTED_EMAIL_ADDRESS" in hint

    def test_hint_for_pii_ssn(self):
        from app.dashboard.api import _make_suppression_hint
        hint = _make_suppression_hint("PII_DETECTED_US_SSN")
        assert hint is not None
        assert "PII_DETECTED_US_SSN" in hint
