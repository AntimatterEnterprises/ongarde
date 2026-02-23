"""Unit tests for E-001-S-007: /health, /health/scanner, ScanLatencyTracker.

Covers all acceptance criteria from E-001-S-007:

  AC-E001-09 — /health endpoint full specification:
    #1:  GET /health returns 503 before app.state.ready = True
    #2:  GET /health returns 200 with ALL required fields after ready
    #3:  connection_pool_size == 100 (M-003 fix from S-002 review)
    #4:  deployment_mode == "self-hosted" when SUPABASE_URL is absent
    #5:  deployment_mode == "managed" when SUPABASE_URL is set
    #6:  GET /health/scanner returns 503 before ready
    #7:  GET /health/scanner returns 200 with all scanner fields after ready

  AC-E001-09 — ScanLatencyTracker (AC item 6):
    #8:  record() + avg_ms computation
    #9:  p99_ms returns 0.0 when fewer than 10 samples
    #10: p99_ms returns non-zero value when >= 10 samples
    #11: count property reflects current sample count
    #12: rolling window evicts oldest samples at maxlen

  AC-E001-09 — check_scanner_health() stub (AC item 7):
    #13: returns ScannerHealth dataclass
    #14: stub always reports healthy=True
    #15: avg and p99 come from the provided latency_tracker

  AC-E001-09 — /health/scanner response fields (AC item 5):
    #16: entity_set from config.scanner.entity_set
    #17: avg_scan_ms and p99_scan_ms fields present
    #18: pool_workers == 0 for lite mode / no pool
    #19: pool_workers == 1 for full mode with pool=None (stub)

  AC-E001-09 — 503 body format (AC item 1):
    #20: 503 body contains status=starting (via error wrapper)
    #21: 503 body contains scanner=initializing

  Regression:
    #22: existing /health 200 fields still present (no regression from S-001)
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.utils.health import ScanLatencyTracker, ScannerHealth, check_scanner_health


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _stub_config() -> Config:
    """Return a default Config (no file I/O)."""
    return Config.defaults()


def _patch_load_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch load_config in app.main to return a stub Config."""
    monkeypatch.setattr("app.main.load_config", lambda: _stub_config())


# ─── AC-1: GET /health — 503 before ready ─────────────────────────────────────


class TestHealth503BeforeReady:
    """AC-E001-09: /health and /health/scanner return 503 before app.state.ready."""

    @pytest.mark.asyncio
    async def test_health_returns_503_before_ready(self) -> None:
        """GET /health returns 503 when app.state.ready is False."""
        application = create_app()
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_health_scanner_returns_503_before_ready(self) -> None:
        """GET /health/scanner returns 503 when app.state.ready is False."""
        application = create_app()
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/scanner")
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_health_503_body_status_starting(self) -> None:
        """503 body contains status=starting (via global error wrapper)."""
        application = create_app()
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        body = response.json()
        # Global exception handler wraps HTTPException.detail in {"error": ...}
        error = body.get("error", {})
        assert isinstance(error, dict), f"Expected dict in 'error' field, got: {body}"
        assert error.get("status") == "starting"

    @pytest.mark.asyncio
    async def test_health_503_body_scanner_initializing(self) -> None:
        """503 body contains scanner=initializing."""
        application = create_app()
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        error = response.json().get("error", {})
        assert error.get("scanner") == "initializing"

    @pytest.mark.asyncio
    async def test_health_503_body_message_field(self) -> None:
        """503 body contains a human-readable 'message' field."""
        application = create_app()
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        error = response.json().get("error", {})
        assert "message" in error
        assert "OnGarde" in error["message"]


# ─── AC-2: GET /health — 200 after ready ──────────────────────────────────────


class TestHealth200AfterReady:
    """AC-E001-09: /health returns 200 with all required fields after startup."""

    def test_health_returns_200_after_lifespan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /health returns 200 after lifespan startup completes."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            response = client.get("/health")
        assert response.status_code == 200

    def test_health_200_has_status_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """200 body has status == 'ok' when scanner is healthy."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        assert body["status"] == "ok"

    def test_health_200_has_proxy_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """200 body has proxy == 'running'."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        assert body["proxy"] == "running"

    def test_health_200_has_scanner_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """200 body has 'scanner' field (healthy/degraded/error)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        assert "scanner" in body
        assert body["scanner"] in ("healthy", "degraded", "error")

    def test_health_200_has_scanner_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """200 body has scanner_mode from config (default: 'full')."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        assert "scanner_mode" in body
        assert body["scanner_mode"] == "full"

    def test_health_200_connection_pool_size_100(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-M-003 fix: connection_pool_size == 100 (configured httpx pool limit)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        assert "connection_pool_size" in body, (
            f"'connection_pool_size' missing from /health response: {body}"
        )
        assert body["connection_pool_size"] == 100

    def test_health_200_has_avg_scan_ms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """200 body has avg_scan_ms field (float)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        assert "avg_scan_ms" in body
        assert isinstance(body["avg_scan_ms"], (int, float))
        assert body["avg_scan_ms"] == 0.0  # stub: no scans yet

    def test_health_200_has_queue_depth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """200 body has queue_depth field (int)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        assert "queue_depth" in body
        assert isinstance(body["queue_depth"], int)
        assert body["queue_depth"] == 0  # stub

    def test_health_200_has_deployment_mode_self_hosted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deployment_mode == 'self-hosted' when SUPABASE_URL is absent."""
        _patch_load_config(monkeypatch)
        application = create_app()
        env_without_supabase = {k: v for k, v in os.environ.items() if k != "SUPABASE_URL"}
        with patch.dict(os.environ, env_without_supabase, clear=True):
            with TestClient(application) as client:
                body = client.get("/health").json()
        assert body["deployment_mode"] == "self-hosted"

    def test_health_200_has_deployment_mode_managed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deployment_mode == 'managed' when SUPABASE_URL is set."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co"}):
            with TestClient(application) as client:
                body = client.get("/health").json()
        assert body["deployment_mode"] == "managed"

    def test_health_200_has_audit_path_self_hosted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """audit_path is a string (not None) for self-hosted mode."""
        _patch_load_config(monkeypatch)
        application = create_app()
        env_without_supabase = {k: v for k, v in os.environ.items() if k != "SUPABASE_URL"}
        with patch.dict(os.environ, env_without_supabase, clear=True):
            with TestClient(application) as client:
                body = client.get("/health").json()
        assert "audit_path" in body
        assert isinstance(body["audit_path"], str)
        assert "audit.db" in body["audit_path"]

    def test_health_200_audit_path_null_managed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """audit_path is null for managed mode."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co"}):
            with TestClient(application) as client:
                body = client.get("/health").json()
        assert body["audit_path"] is None

    def test_health_200_all_required_fields_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ALL required fields from AC-E001-09 are present in the 200 response."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        required = {
            "status",
            "proxy",
            "scanner",
            "scanner_mode",
            "connection_pool_size",
            "avg_scan_ms",
            "queue_depth",
            "deployment_mode",
            "audit_path",
        }
        missing = required - set(body.keys())
        assert not missing, f"Missing required /health fields: {missing}\nResponse: {body}"


# ─── AC-4: GET /health/scanner ─────────────────────────────────────────────────


class TestHealthScannerEndpoint:
    """AC-E001-09: /health/scanner returns correct fields after startup."""

    def test_health_scanner_returns_200_after_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /health/scanner returns 200 after lifespan startup."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            response = client.get("/health/scanner")
        assert response.status_code == 200

    def test_health_scanner_has_scanner_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response has 'scanner' field."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "scanner" in body
        assert body["scanner"] in ("healthy", "degraded", "error")

    def test_health_scanner_has_scanner_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response has scanner_mode from config."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "scanner_mode" in body
        assert body["scanner_mode"] == "full"  # default config

    def test_health_scanner_has_entity_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response has entity_set from config (default 5 entities)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "entity_set" in body
        assert isinstance(body["entity_set"], list)
        assert len(body["entity_set"]) > 0
        # Default entity set matches config.scanner.entity_set defaults
        expected_defaults = {"CREDIT_CARD", "CRYPTO", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN"}
        assert expected_defaults.issubset(set(body["entity_set"])), (
            f"entity_set missing defaults: {expected_defaults - set(body['entity_set'])}"
        )

    def test_health_scanner_has_avg_scan_ms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response has avg_scan_ms (float, stub: 0.0)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "avg_scan_ms" in body
        assert isinstance(body["avg_scan_ms"], (int, float))

    def test_health_scanner_has_p99_scan_ms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response has p99_scan_ms (float, stub: 0.0)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "p99_scan_ms" in body
        assert isinstance(body["p99_scan_ms"], (int, float))

    def test_health_scanner_has_queue_depth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response has queue_depth (int, stub: 0)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "queue_depth" in body
        assert isinstance(body["queue_depth"], int)

    def test_health_scanner_has_pool_workers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response has pool_workers (int)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "pool_workers" in body
        assert isinstance(body["pool_workers"], int)

    def test_health_scanner_pool_workers_zero_when_no_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pool_workers == 0 when scan_pool is None (e.g., pool failed to initialize)."""
        from app.scanner.calibration import CalibrationResult
        from unittest.mock import AsyncMock
        _patch_load_config(monkeypatch)
        # Mock startup_scan_pool to return None pool (simulates pool init failure)
        monkeypatch.setattr(
            "app.main.startup_scan_pool",
            AsyncMock(return_value=(None, CalibrationResult.conservative_fallback("test")))
        )
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        # None pool → 0 workers
        assert body["pool_workers"] == 0

    def test_health_scanner_all_required_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ALL required fields from AC-E001-09 item 5 are present."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        required = {
            "scanner",
            "scanner_mode",
            "entity_set",
            "avg_scan_ms",
            "p99_scan_ms",
            "queue_depth",
            "pool_workers",
        }
        missing = required - set(body.keys())
        assert not missing, (
            f"Missing required /health/scanner fields: {missing}\nResponse: {body}"
        )


# ─── AC-6: ScanLatencyTracker ─────────────────────────────────────────────────


class TestScanLatencyTracker:
    """AC-E001-09 item 6: ScanLatencyTracker interface and behaviour."""

    def test_avg_ms_empty_window_is_zero(self) -> None:
        """avg_ms returns 0.0 when no samples have been recorded."""
        tracker = ScanLatencyTracker()
        assert tracker.avg_ms == 0.0

    def test_avg_ms_single_sample(self) -> None:
        """avg_ms equals the single recorded value."""
        tracker = ScanLatencyTracker()
        tracker.record(42.0)
        assert tracker.avg_ms == 42.0

    def test_avg_ms_multiple_samples(self) -> None:
        """avg_ms computes the arithmetic mean of all samples."""
        tracker = ScanLatencyTracker()
        tracker.record(10.0)
        tracker.record(20.0)
        tracker.record(30.0)
        assert tracker.avg_ms == 20.0

    def test_p99_returns_zero_below_10_samples(self) -> None:
        """p99_ms returns 0.0 when fewer than 10 samples are available."""
        tracker = ScanLatencyTracker()
        for i in range(9):
            tracker.record(float(i))
        assert tracker.p99_ms == 0.0

    def test_p99_returns_nonzero_at_10_samples(self) -> None:
        """p99_ms returns a non-zero value once 10+ samples are present."""
        tracker = ScanLatencyTracker()
        for i in range(9):
            tracker.record(float(i))
        tracker.record(100.0)  # 10th sample
        assert tracker.p99_ms > 0.0

    def test_p99_reasonable_value(self) -> None:
        """p99_ms is at or near the high end of the distribution."""
        tracker = ScanLatencyTracker()
        # 10 samples: 1..10 ms
        for i in range(1, 11):
            tracker.record(float(i))
        # p99 of [1,2,...,10] should be close to 10
        assert tracker.p99_ms >= 8.0

    def test_count_starts_at_zero(self) -> None:
        """count is 0 before any samples are recorded."""
        tracker = ScanLatencyTracker()
        assert tracker.count == 0

    def test_count_increments_with_records(self) -> None:
        """count increments with each recorded sample."""
        tracker = ScanLatencyTracker()
        for i in range(5):
            tracker.record(float(i))
        assert tracker.count == 5

    def test_rolling_window_enforces_maxlen(self) -> None:
        """Samples exceeding window size evict the oldest entries."""
        tracker = ScanLatencyTracker(window=100)
        for i in range(150):
            tracker.record(float(i))
        # Only last 100 samples kept
        assert tracker.count == 100

    def test_rolling_window_evicts_oldest(self) -> None:
        """After overflow, avg_ms reflects only the most recent window."""
        tracker = ScanLatencyTracker(window=3)
        tracker.record(1.0)
        tracker.record(2.0)
        tracker.record(3.0)
        # Window full: [1, 2, 3], avg=2.0
        assert tracker.avg_ms == 2.0
        # Push oldest out: [2, 3, 99]
        tracker.record(99.0)
        assert tracker.count == 3
        assert tracker.avg_ms == pytest.approx((2 + 3 + 99) / 3)

    def test_custom_window_size(self) -> None:
        """ScanLatencyTracker respects a custom window size."""
        tracker = ScanLatencyTracker(window=5)
        for i in range(10):
            tracker.record(float(i))
        assert tracker.count == 5

    def test_record_returns_none(self) -> None:
        """record() returns None (no return value expected)."""
        tracker = ScanLatencyTracker()
        result = tracker.record(1.0)
        assert result is None


# ─── AC-7: check_scanner_health() stub ────────────────────────────────────────


class TestCheckScannerHealth:
    """AC-E001-09 item 7: check_scanner_health() stub behaviour."""

    @pytest.mark.asyncio
    async def test_returns_scanner_health_dataclass(self) -> None:
        """check_scanner_health returns a ScannerHealth instance."""
        result = await check_scanner_health(scan_pool=None)
        assert isinstance(result, ScannerHealth)

    @pytest.mark.asyncio
    async def test_stub_always_healthy(self) -> None:
        """Stub always returns healthy=True (real check in E-003)."""
        result = await check_scanner_health(scan_pool=None)
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_stub_queue_depth_zero(self) -> None:
        """Stub always returns queue_depth=0 (real introspection in E-003)."""
        result = await check_scanner_health(scan_pool=None)
        assert result.queue_depth == 0

    @pytest.mark.asyncio
    async def test_uses_provided_latency_tracker(self) -> None:
        """Latency values come from the provided ScanLatencyTracker."""
        tracker = ScanLatencyTracker()
        for i in range(1, 6):
            tracker.record(float(i * 10))
        result = await check_scanner_health(scan_pool=None, latency_tracker=tracker)
        assert result.avg_latency_ms == tracker.avg_ms
        assert result.p99_latency_ms == tracker.p99_ms

    @pytest.mark.asyncio
    async def test_zero_latency_when_no_tracker(self) -> None:
        """Returns 0.0 latencies when no tracker is provided."""
        result = await check_scanner_health(scan_pool=None, latency_tracker=None)
        assert result.avg_latency_ms == 0.0
        assert result.p99_latency_ms == 0.0

    @pytest.mark.asyncio
    async def test_healthy_when_pool_is_none(self) -> None:
        """Stub returns healthy even when scan_pool is None (lite mode / pre-E-003)."""
        result = await check_scanner_health(scan_pool=None)
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_healthy_when_pool_provided(self) -> None:
        """Stub returns healthy regardless of pool value (real check in E-003)."""
        result = await check_scanner_health(scan_pool=object())  # any non-None object
        assert result.healthy is True


# ─── Regression: S-001 fields not removed ─────────────────────────────────────


class TestHealthRegressionFromS001:
    """Regression: ensure S-001 required fields are still present in /health 200."""

    def test_s001_required_fields_still_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fields required by E-001-S-001 AC-3 are not removed by S-007."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health").json()
        # S-001 required: proxy, scanner, scanner_mode
        assert body.get("proxy") == "running"
        assert "scanner" in body
        assert "scanner_mode" in body

    def test_503_body_format_unchanged(self) -> None:
        """S-001 503 body format is preserved (error wrapper + status/scanner keys)."""
        application = create_app()
        import asyncio
        from httpx import ASGITransport, AsyncClient

        async def get_503() -> Any:
            transport = ASGITransport(app=application)  # type: ignore[arg-type]
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                return (await c.get("/health")).json()

        body = asyncio.get_event_loop().run_until_complete(get_503())
        error = body.get("error", {})
        assert error.get("status") == "starting"
        assert error.get("scanner") == "initializing"


# ─── Latency tracker wired into app.state ─────────────────────────────────────


class TestLatencyTrackerInAppState:
    """Verify ScanLatencyTracker is initialised in lifespan and stored in app.state."""

    def test_latency_tracker_in_app_state_after_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """app.state.latency_tracker is a ScanLatencyTracker after lifespan startup."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as _client:
            tracker = getattr(application.state, "latency_tracker", None)
            assert tracker is not None, "app.state.latency_tracker was not set by lifespan"
            assert isinstance(tracker, ScanLatencyTracker)

    def test_latency_tracker_empty_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Latency tracker starts empty (count=0, avg=0.0)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as _client:
            tracker: ScanLatencyTracker = application.state.latency_tracker
            assert tracker.count == 0
            assert tracker.avg_ms == 0.0
            assert tracker.p99_ms == 0.0

    def test_avg_scan_ms_reflects_tracker_recordings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/health avg_scan_ms reflects latency_tracker recordings made during runtime."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            # Manually record some latencies (simulates scan pipeline in E-002+)
            application.state.latency_tracker.record(10.0)
            application.state.latency_tracker.record(20.0)
            body = client.get("/health").json()
        assert body["avg_scan_ms"] == 15.0


# ─── Calibration field in /health/scanner (HOLD-002 fix) ─────────────────────


class TestHealthScannerCalibrationField:
    """Integration tests: /health/scanner includes calibration field (HOLD-002 fix).

    Verifies the Adaptive Performance Protocol exposes calibration results via
    the /health/scanner endpoint, so operators can see what tier their hardware
    is running at and whether calibration succeeded or fell back to defaults.
    """

    def test_health_scanner_includes_calibration_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/health/scanner response includes a 'calibration' field."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        assert "calibration" in body, (
            f"'calibration' field missing from /health/scanner response: {body}"
        )

    def test_health_scanner_calibration_has_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """calibration.tier is one of the valid hardware tier strings."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        assert "tier" in calibration
        assert calibration["tier"] in ("fast", "standard", "slow", "minimal", "unknown")

    def test_health_scanner_calibration_has_sync_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """calibration.sync_cap is an integer ≥ 0."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        assert "sync_cap" in calibration
        assert isinstance(calibration["sync_cap"], int)
        assert calibration["sync_cap"] >= 0

    def test_health_scanner_calibration_has_timeout_ms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """calibration.timeout_ms is a number > 0."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        assert "timeout_ms" in calibration
        assert isinstance(calibration["timeout_ms"], (int, float))
        assert calibration["timeout_ms"] > 0

    def test_health_scanner_calibration_has_measured_p99_ms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """calibration.measured_p99_ms is a dict (may be empty for stub/fallback)."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        assert "measured_p99_ms" in calibration
        assert isinstance(calibration["measured_p99_ms"], dict)

    def test_health_scanner_calibration_has_calibration_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """calibration.calibration_ok is a boolean."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        assert "calibration_ok" in calibration
        assert isinstance(calibration["calibration_ok"], bool)

    def test_health_scanner_calibration_has_fallback_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """calibration.fallback_reason is a string or null."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        assert "fallback_reason" in calibration
        # In stub mode (E-001), calibration fails → fallback_reason is a string
        assert calibration["fallback_reason"] is None or isinstance(
            calibration["fallback_reason"], str
        )

    def test_health_scanner_calibration_fallback_when_pool_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When pool fails to init, calibration uses conservative fallback → calibration_ok=False."""
        from app.scanner.calibration import CalibrationResult
        from unittest.mock import AsyncMock
        _patch_load_config(monkeypatch)
        # Simulate pool init failure → conservative fallback
        monkeypatch.setattr(
            "app.main.startup_scan_pool",
            AsyncMock(return_value=(None, CalibrationResult.conservative_fallback("pool init failed")))
        )
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        # Fallback → calibration_ok=False, fallback_reason is set
        assert calibration["calibration_ok"] is False
        assert calibration["fallback_reason"] is not None

    def test_health_scanner_calibration_all_required_subfields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """calibration dict has ALL required subfields."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        calibration = body["calibration"]
        required = {"tier", "sync_cap", "timeout_ms", "measured_p99_ms", "calibration_ok", "fallback_reason"}
        missing = required - set(calibration.keys())
        assert not missing, (
            f"Missing calibration subfields: {missing}\nCalibration: {calibration}"
        )

    def test_health_scanner_all_fields_including_calibration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ALL required /health/scanner fields present — including calibration."""
        _patch_load_config(monkeypatch)
        application = create_app()
        with TestClient(application) as client:
            body = client.get("/health/scanner").json()
        required = {
            "scanner",
            "scanner_mode",
            "entity_set",
            "avg_scan_ms",
            "p99_scan_ms",
            "queue_depth",
            "pool_workers",
            "calibration",  # HOLD-002 addition
        }
        missing = required - set(body.keys())
        assert not missing, (
            f"Missing required /health/scanner fields: {missing}\nResponse: {body}"
        )
