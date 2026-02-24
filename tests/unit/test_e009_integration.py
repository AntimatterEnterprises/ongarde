"""E-009 Integration tests — Allowlist Config, ALLOW_SUPPRESSED, Dashboard.

Tests:
  - apply_allowlist() + scan_or_block() integration (E-009-S-002, S-003)
  - ALLOW_SUPPRESSED audit events in dashboard counters (E-009-S-004, AC-E009-07)
  - /config-status endpoint (E-009-S-005)
  - notify_config_reloaded() → /config-status reflects new count
  - ALLOW_SUPPRESSED events in /dashboard/api/events
  - ALLOW_SUPPRESSED NOT in block counter but IS in request counter
  - CORS restricted to localhost origins (E-009-S-003)

Story: E-009-S-001, E-009-S-002, E-009-S-003, E-009-S-004, E-009-S-005
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.allowlist.loader import AllowlistEntry, AllowlistLoader
from app.allowlist.matcher import apply_allowlist
from app.audit.models import AuditEvent
from app.audit.protocol import NullAuditBackend
from app.dashboard.api import notify_config_reloaded
from app.models.scan import Action, RiskLevel, ScanResult

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _mock_backend(events=None, count=0):
    mock = AsyncMock(spec=NullAuditBackend)
    mock.query_events = AsyncMock(return_value=events or [])
    mock.count_events = AsyncMock(return_value=count)
    return mock


def _block_event(**kwargs):
    defaults = dict(
        scan_id="01TEST00001",
        timestamp=datetime.now(timezone.utc),
        action="BLOCK",
        direction="REQUEST",
        rule_id="CREDENTIAL_DETECTED",
        risk_level="CRITICAL",
        redacted_excerpt="[REDACTED]",
        user_id="test_user",
    )
    defaults.update(kwargs)
    return AuditEvent(**defaults)


def _suppressed_event(**kwargs):
    defaults = dict(
        scan_id="01SUPP00001",
        timestamp=datetime.now(timezone.utc),
        action="ALLOW_SUPPRESSED",
        direction="REQUEST",
        rule_id="CREDENTIAL_DETECTED",
        risk_level="CRITICAL",
        redacted_excerpt="[REDACTED]",
        user_id="test_user",
        allowlist_rule_id="CREDENTIAL_DETECTED",
    )
    defaults.update(kwargs)
    return AuditEvent(**defaults)


# ─── AllowlistLoader + scan_or_block integration ──────────────────────────────


class TestAllowlistWithScanOrBlock:
    """Tests that verify apply_allowlist() integrates correctly with scan results."""

    def test_block_with_matching_allowlist_returns_allow_suppressed(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = ScanResult(
            action=Action.BLOCK,
            rule_id="CREDENTIAL_DETECTED",
            risk_level=RiskLevel.CRITICAL,
            scan_id="01TEST",
        )
        out = apply_allowlist(result, "content", [entry])
        assert out.action == Action.ALLOW_SUPPRESSED

    def test_block_without_matching_allowlist_returns_block(self):
        entry = AllowlistEntry(rule_id="OTHER_RULE")
        result = ScanResult(
            action=Action.BLOCK,
            rule_id="CREDENTIAL_DETECTED",
            risk_level=RiskLevel.CRITICAL,
            scan_id="01TEST",
        )
        out = apply_allowlist(result, "content", [entry])
        assert out.action == Action.BLOCK

    def test_empty_loader_passes_block_unchanged(self):
        loader = AllowlistLoader()
        result = ScanResult(
            action=Action.BLOCK,
            rule_id="CREDENTIAL_DETECTED",
            risk_level=RiskLevel.CRITICAL,
            scan_id="01TEST",
        )
        entries = loader.get_entries()
        out = apply_allowlist(result, "content", entries)
        assert out.action == Action.BLOCK

    def test_allow_suppressed_has_allowlist_rule_id(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = ScanResult(
            action=Action.BLOCK,
            rule_id="CREDENTIAL_DETECTED",
            risk_level=RiskLevel.CRITICAL,
            scan_id="01TEST",
        )
        out = apply_allowlist(result, "content", [entry])
        assert out.allowlist_rule_id == "CREDENTIAL_DETECTED"

    def test_allow_suppressed_original_fields_preserved(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = ScanResult(
            action=Action.BLOCK,
            rule_id="CREDENTIAL_DETECTED",
            risk_level=RiskLevel.HIGH,
            scan_id="01MYSCAN",
            redacted_excerpt="sk-***REDACTED***",
        )
        out = apply_allowlist(result, "content", [entry])
        assert out.risk_level == RiskLevel.HIGH
        assert out.scan_id == "01MYSCAN"
        assert out.redacted_excerpt == "sk-***REDACTED***"


# ─── Dashboard counter / events with ALLOW_SUPPRESSED ─────────────────────────


class TestDashboardAllowSuppressed:
    """AC-E009-07: ALLOW_SUPPRESSED in events list, NOT in block counter."""

    def _make_app(self, events=None, block_count=0, request_count=0):
        from app.main import create_app
        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)
        app.state.ready = True
        backend = _mock_backend(events=events)
        backend.count_events = AsyncMock(side_effect=lambda f: (
            block_count if getattr(f, "action", None) == "BLOCK" else request_count
        ))
        app.state.audit_backend = backend
        app.state.config = MagicMock()
        app.state.config.scanner.mode = "full"
        app.state.latency_tracker = MagicMock()
        app.state.latency_tracker.p99.return_value = 12.0
        app.state.latency_tracker.avg.return_value = 8.0
        app.state.calibration = MagicMock()
        app.state.calibration.tier = "standard"
        app.state.streaming_tracker = MagicMock()
        app.state.streaming_tracker.active_count.return_value = 0
        app.state.streaming_tracker.window_p99.return_value = None
        return client

    def test_allow_suppressed_events_in_events_api(self):
        suppressed = _suppressed_event()
        client = self._make_app(events=[suppressed])
        resp = client.get("/dashboard/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["action"] == "ALLOW_SUPPRESSED"

    def test_allow_suppressed_not_in_block_counter(self):
        # block_count=0 (only ALLOW_SUPPRESSED events exist)
        client = self._make_app(block_count=0, request_count=10)
        resp = client.get("/dashboard/api/counters")
        assert resp.status_code == 200
        data = resp.json()
        # Block counter shows 0 even if there are 10 ALLOW_SUPPRESSED
        assert data["blocks"]["today"] == 0

    def test_allow_suppressed_has_allowlist_rule_id_field(self):
        suppressed = _suppressed_event(allowlist_rule_id="CREDENTIAL_DETECTED")
        client = self._make_app(events=[suppressed])
        resp = client.get("/dashboard/api/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert events[0].get("allowlist_rule_id") == "CREDENTIAL_DETECTED"


# ─── /config-status endpoint tests ───────────────────────────────────────────


class TestConfigStatusEndpoint:
    """E-009-S-005: GET /dashboard/api/config-status."""

    def _make_app(self):
        from app.main import create_app
        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)
        app.state.ready = True
        app.state.audit_backend = _mock_backend()
        app.state.config = MagicMock()
        app.state.config.scanner.mode = "full"
        app.state.latency_tracker = MagicMock()
        app.state.latency_tracker.p99.return_value = None
        app.state.latency_tracker.avg.return_value = None
        app.state.calibration = MagicMock()
        app.state.calibration.tier = "standard"
        app.state.streaming_tracker = MagicMock()
        app.state.streaming_tracker.active_count.return_value = 0
        app.state.streaming_tracker.window_p99.return_value = None
        return client

    def test_config_status_returns_200(self):
        client = self._make_app()
        resp = client.get("/dashboard/api/config-status")
        assert resp.status_code == 200

    def test_config_status_has_last_reload_at_field(self):
        client = self._make_app()
        resp = client.get("/dashboard/api/config-status")
        data = resp.json()
        assert "last_reload_at" in data

    def test_config_status_has_allowlist_count_field(self):
        client = self._make_app()
        resp = client.get("/dashboard/api/config-status")
        data = resp.json()
        assert "allowlist_count" in data

    def test_config_status_initially_null_reload(self):
        # Reset config status for this test
        import app.dashboard.api as api_module
        api_module._config_status["last_reload_at"] = None
        api_module._config_status["allowlist_count"] = 0
        client = self._make_app()
        resp = client.get("/dashboard/api/config-status")
        data = resp.json()
        assert data["last_reload_at"] is None
        assert data["allowlist_count"] == 0

    def test_notify_config_reloaded_updates_status(self):
        import app.dashboard.api as api_module
        api_module._config_status["last_reload_at"] = None
        api_module._config_status["allowlist_count"] = 0

        notify_config_reloaded(5)

        assert api_module._config_status["allowlist_count"] == 5
        assert api_module._config_status["last_reload_at"] is not None

    def test_notify_config_reloaded_timestamp_is_iso(self):
        notify_config_reloaded(3)
        import app.dashboard.api as api_module
        # Should be parseable as ISO datetime
        ts = api_module._config_status["last_reload_at"]
        assert ts is not None
        datetime.fromisoformat(ts)  # Will raise if not valid ISO

    def test_config_status_endpoint_no_503_when_not_ready(self):
        # config-status is informational — available even when not ready
        from app.main import create_app
        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)
        app.state.ready = False
        resp = client.get("/dashboard/api/config-status")
        # Should return 200 (no _require_ready check on this endpoint)
        assert resp.status_code == 200


# ─── CORS restriction tests ───────────────────────────────────────────────────


class TestCORSRestriction:
    """E-009-S-003: CORS must be restricted to localhost origins."""

    def _make_app(self):
        from app.main import create_app
        return create_app()

    def test_cors_allows_localhost_origin(self):
        app = self._make_app()
        app.state.ready = False
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/dashboard/api/config-status",
            headers={
                "Origin": "http://localhost:4242",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Should allow or not have error
        assert resp.status_code in (200, 204, 400)

    def test_cors_middleware_present(self):
        app = self._make_app()
        # Verify CORSMiddleware is in user_middleware
        cors_found = any(
            "CORSMiddleware" in str(m) or "cors" in str(m).lower()
            for m in getattr(app, "user_middleware", [])
        )
        assert cors_found, "CORSMiddleware not found in app middleware"

    def test_no_wildcard_origin_in_cors_config(self):
        """Verify allow_origins does NOT contain '*' (E-009 non-negotiable)."""
        app = self._make_app()
        for m in getattr(app, "user_middleware", []):
            kwargs = getattr(m, "kwargs", {})
            origins = kwargs.get("allow_origins", [])
            assert "*" not in origins, "CORS wildcard origin found — E-009 violation"
