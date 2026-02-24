"""Unit tests for StreamingMetricsTracker — E-004-S-005.

Tests:
  - stream_opened/stream_closed increment/decrement active_count
  - stream_closed never goes below 0
  - record_window_scan populates latency measurements
  - window_avg_ms correct after N measurements
  - window_p99_ms returns 0.0 with < 10 samples, correct with ≥ 10
  - /health/scanner response includes streaming fields (backward-compatible)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.utils.health import StreamingMetricsTracker

# ─── StreamingMetricsTracker Unit Tests ──────────────────────────────────────

class TestStreamingMetricsTracker:

    def test_initial_active_count_is_zero(self):
        tracker = StreamingMetricsTracker()
        assert tracker.active_count == 0

    def test_stream_opened_increments_active_count(self):
        tracker = StreamingMetricsTracker()
        tracker.stream_opened()
        assert tracker.active_count == 1

    def test_stream_opened_multiple_times(self):
        tracker = StreamingMetricsTracker()
        for _ in range(3):
            tracker.stream_opened()
        assert tracker.active_count == 3

    def test_stream_closed_decrements_active_count(self):
        tracker = StreamingMetricsTracker()
        tracker.stream_opened()
        tracker.stream_opened()
        tracker.stream_closed()
        assert tracker.active_count == 1

    def test_stream_closed_never_goes_below_zero(self):
        """stream_closed() on count=0 doesn't go negative (edge case)."""
        tracker = StreamingMetricsTracker()
        tracker.stream_closed()  # Should not raise or go negative
        assert tracker.active_count == 0

    def test_stream_opened_and_closed_pairs(self):
        tracker = StreamingMetricsTracker()
        tracker.stream_opened()
        tracker.stream_opened()
        tracker.stream_closed()
        tracker.stream_closed()
        assert tracker.active_count == 0

    def test_record_window_scan_populates_measurements(self):
        tracker = StreamingMetricsTracker()
        tracker.record_window_scan(0.25)
        assert tracker.window_count == 1

    def test_window_count_increments_per_measurement(self):
        tracker = StreamingMetricsTracker()
        for i in range(5):
            tracker.record_window_scan(0.1 * i)
        assert tracker.window_count == 5

    def test_window_avg_ms_correct(self):
        tracker = StreamingMetricsTracker()
        tracker.record_window_scan(0.10)
        tracker.record_window_scan(0.20)
        tracker.record_window_scan(0.30)
        assert abs(tracker.window_avg_ms - 0.20) < 0.001

    def test_window_avg_ms_returns_zero_when_empty(self):
        tracker = StreamingMetricsTracker()
        assert tracker.window_avg_ms == 0.0

    def test_window_p99_returns_zero_with_fewer_than_10_samples(self):
        tracker = StreamingMetricsTracker()
        for i in range(9):
            tracker.record_window_scan(0.1)
        assert tracker.window_p99_ms == 0.0

    def test_window_p99_correct_with_10_plus_samples(self):
        tracker = StreamingMetricsTracker()
        # 100 samples: 95 × 0.1ms + 5 × 5.0ms (outliers at top)
        for _ in range(95):
            tracker.record_window_scan(0.1)
        for _ in range(5):
            tracker.record_window_scan(5.0)
        # p99 of 100 samples: index = max(0, int(100 * 0.99) - 1) = 98
        # sorted[98] = one of the 5.0ms outliers
        p99 = tracker.window_p99_ms
        assert p99 > 0.1, f"p99={p99} should be greater than median 0.1"
        assert p99 <= 5.0

    def test_window_p99_with_100_samples(self):
        tracker = StreamingMetricsTracker()
        for i in range(99):
            tracker.record_window_scan(0.25)
        tracker.record_window_scan(1.5)  # The outlier at p99
        p99 = tracker.window_p99_ms
        assert p99 >= 0.25, "p99 should be at least the median value"

    def test_rolling_window_evicts_old_samples(self):
        """Rolling window of 5 evicts oldest when adding the 6th."""
        tracker = StreamingMetricsTracker(window=5)
        for i in range(5):
            tracker.record_window_scan(1.0)
        assert tracker.window_count == 5
        tracker.record_window_scan(0.1)
        assert tracker.window_count == 5  # Evicted oldest


# ─── Test: /health/scanner streaming fields ──────────────────────────────────

class TestHealthScannerStreamingFields:
    """Verify /health/scanner includes streaming fields (backward-compatible)."""

    @pytest.mark.asyncio
    async def test_health_scanner_includes_streaming_active(self):
        """GET /health/scanner returns streaming_active field."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # We test the health router directly with a mock app state
        from app.health import router

        app = FastAPI()
        app.include_router(router)

        # Set up mock state
        app.state.ready = True
        app.state.scan_pool = None
        app.state.config = _make_mock_config()
        app.state.latency_tracker = None
        app.state.calibration = None
        streaming_tracker = StreamingMetricsTracker()
        streaming_tracker.stream_opened()  # 1 active stream
        streaming_tracker.record_window_scan(0.25)
        streaming_tracker.record_window_scan(0.30)
        app.state.streaming_tracker = streaming_tracker

        client = TestClient(app)
        resp = client.get("/health/scanner")
        assert resp.status_code == 200
        data = resp.json()

        # Verify streaming fields present
        assert "streaming_active" in data
        assert data["streaming_active"] == 1
        assert "window_scan_avg_ms" in data
        assert "window_scan_p99_ms" in data
        assert "window_scan_count" in data

    @pytest.mark.asyncio
    async def test_health_scanner_existing_fields_unchanged(self):
        """/health/scanner existing fields unchanged (backward-compatible)."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.health import router

        app = FastAPI()
        app.include_router(router)

        app.state.ready = True
        app.state.scan_pool = None
        app.state.config = _make_mock_config()
        app.state.latency_tracker = None
        app.state.calibration = None
        app.state.streaming_tracker = StreamingMetricsTracker()

        client = TestClient(app)
        resp = client.get("/health/scanner")
        assert resp.status_code == 200
        data = resp.json()

        # Verify existing fields still present
        required_existing = [
            "scanner", "scanner_mode", "entity_set",
            "avg_scan_ms", "p99_scan_ms", "queue_depth",
            "pool_workers", "calibration",
        ]
        for field in required_existing:
            assert field in data, f"Existing field '{field}' missing from /health/scanner"

    @pytest.mark.asyncio
    async def test_health_scanner_no_tracker_returns_zeros(self):
        """/health/scanner without streaming_tracker returns zeros for streaming fields."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.health import router

        app = FastAPI()
        app.include_router(router)

        app.state.ready = True
        app.state.scan_pool = None
        app.state.config = _make_mock_config()
        app.state.latency_tracker = None
        app.state.calibration = None
        # No streaming_tracker on state

        client = TestClient(app)
        resp = client.get("/health/scanner")
        assert resp.status_code == 200
        data = resp.json()

        assert data["streaming_active"] == 0
        assert data["window_scan_avg_ms"] == 0.0
        assert data["window_scan_p99_ms"] == 0.0


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_mock_config():
    """Create a minimal mock config for health endpoint tests."""
    config = MagicMock()
    config.scanner.mode = "lite"
    config.scanner.entity_set = []
    return config
