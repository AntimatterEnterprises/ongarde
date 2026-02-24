"""Unit tests for app/scanner/calibration.py — Adaptive Performance Protocol.

Tests the calibration threshold derivation logic using mocked measurements.
No real Presidio or ProcessPoolExecutor required — pure logic tests.

Coverage:
  - CalibrationResult dataclass and conservative_fallback()
  - derive_thresholds() — all tier classifications and edge cases
  - Config override path (tested via main.py integration)
  - Calibration failure fallback (conservative defaults)
  - _make_calibration_text() length correctness

Architecture reference: architecture.md §13 (Adaptive Performance Protocol)
Story: HOLD-002 fix — Adaptive Performance Protocol
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from app.constants import (
    DEFAULT_PRESIDIO_SYNC_CAP,
    PRESIDIO_CALIBRATION_SIZES,
    PRESIDIO_TIMEOUT_FALLBACK_S,
    PRESIDIO_TIMEOUT_MAX_S,
    PRESIDIO_TIMEOUT_MIN_S,
)
from app.scanner.calibration import (
    CalibrationResult,
    _make_calibration_text,
    derive_thresholds,
    run_calibration,
)

# ─── CalibrationResult ────────────────────────────────────────────────────────


class TestCalibrationResult:
    """Tests for CalibrationResult dataclass."""

    def test_timeout_ms_converts_correctly(self) -> None:
        """timeout_ms property returns timeout_s × 1000."""
        result = CalibrationResult(sync_cap=1000, timeout_s=0.045, tier="standard")
        assert result.timeout_ms == pytest.approx(45.0)

    def test_timeout_ms_for_25ms(self) -> None:
        """timeout_ms works for 25ms minimum."""
        result = CalibrationResult(sync_cap=500, timeout_s=0.025, tier="slow")
        assert result.timeout_ms == pytest.approx(25.0)

    def test_measured_p99_at_sync_cap_returns_correct_measurement(self) -> None:
        """measured_p99_at_sync_cap_ms returns p99 for the sync_cap size."""
        measurements = {200: 8.1, 500: 18.4, 1000: 29.3}
        result = CalibrationResult(
            sync_cap=1000, timeout_s=0.044, tier="standard", measurements=measurements
        )
        assert result.measured_p99_at_sync_cap_ms == pytest.approx(29.3)

    def test_measured_p99_at_sync_cap_500(self) -> None:
        """measured_p99_at_sync_cap_ms returns 500-char p99 when sync_cap=500."""
        measurements = {200: 10.0, 500: 22.5}
        result = CalibrationResult(
            sync_cap=500, timeout_s=0.034, tier="slow", measurements=measurements
        )
        assert result.measured_p99_at_sync_cap_ms == pytest.approx(22.5)

    def test_measured_p99_at_sync_cap_zero_returns_smallest_size(self) -> None:
        """When sync_cap=0, returns p99 for smallest calibration size."""
        measurements = {200: 35.0}
        result = CalibrationResult(
            sync_cap=0, timeout_s=0.060, tier="minimal", measurements=measurements
        )
        # Smallest size in PRESIDIO_CALIBRATION_SIZES is 200
        assert result.measured_p99_at_sync_cap_ms == pytest.approx(35.0)

    def test_measured_p99_returns_none_when_not_measured(self) -> None:
        """Returns None when the sync_cap size wasn't measured."""
        result = CalibrationResult(
            sync_cap=1000, timeout_s=0.060, tier="minimal", measurements={}
        )
        assert result.measured_p99_at_sync_cap_ms is None

    def test_calibration_ok_defaults_true(self) -> None:
        """New CalibrationResult has calibration_ok=True by default."""
        result = CalibrationResult(sync_cap=500, timeout_s=0.030, tier="slow")
        assert result.calibration_ok is True
        assert result.fallback_reason is None

    def test_conservative_fallback_factory(self) -> None:
        """conservative_fallback() creates a safe fallback CalibrationResult."""
        reason = "pool startup failed: connection timeout"
        result = CalibrationResult.conservative_fallback(reason)

        assert result.sync_cap == DEFAULT_PRESIDIO_SYNC_CAP
        assert result.timeout_s == pytest.approx(PRESIDIO_TIMEOUT_FALLBACK_S)
        assert result.tier == "minimal"
        assert result.calibration_ok is False
        assert result.fallback_reason == reason
        assert result.measurements == {}

    def test_conservative_fallback_is_safe_on_any_hardware(self) -> None:
        """Conservative fallback values must be safe (non-negative, bounded)."""
        result = CalibrationResult.conservative_fallback("test")
        assert result.sync_cap >= 0
        assert result.timeout_s >= PRESIDIO_TIMEOUT_MIN_S
        assert result.timeout_s <= PRESIDIO_TIMEOUT_MAX_S


# ─── derive_thresholds ────────────────────────────────────────────────────────


class TestDeriveThresholds:
    """Tests for derive_thresholds() — the pure threshold derivation function."""

    # ── Tier: fast ───────────────────────────────────────────────────────────

    def test_fast_tier_all_sizes_well_under_20ms(self) -> None:
        """Fast tier: 1000-char p99 ≤ 20ms."""
        measurements = {200: 5.0, 500: 10.0, 1000: 15.0}
        result = derive_thresholds(measurements)

        assert result.tier == "fast"
        assert result.sync_cap == 1000
        assert result.calibration_ok is True

    def test_fast_tier_at_exactly_20ms(self) -> None:
        """Fast tier boundary: 1000-char p99 = 20ms exactly."""
        measurements = {200: 8.0, 500: 15.0, 1000: 20.0}
        result = derive_thresholds(measurements)

        assert result.tier == "fast"
        assert result.sync_cap == 1000

    def test_fast_tier_timeout_derivation(self) -> None:
        """Fast tier timeout = 1000-char p99 × 1.5, clamped to [25ms, 60ms]."""
        measurements = {200: 5.0, 500: 10.0, 1000: 15.0}
        result = derive_thresholds(measurements)

        # 15ms × 1.5 = 22.5ms → clamps to 25ms (minimum)
        assert result.timeout_s == pytest.approx(PRESIDIO_TIMEOUT_MIN_S)

    def test_fast_tier_timeout_above_minimum(self) -> None:
        """Fast tier: if p99 × 1.5 > 25ms, use the computed value."""
        measurements = {200: 8.0, 500: 14.0, 1000: 18.0}
        result = derive_thresholds(measurements)

        # 18ms × 1.5 = 27ms → 0.027s, above minimum
        expected_timeout_s = 0.018 * 1.5  # = 0.027
        assert result.timeout_s == pytest.approx(expected_timeout_s, rel=1e-3)

    # ── Tier: standard ──────────────────────────────────────────────────────

    def test_standard_tier_1000_char_at_25ms(self) -> None:
        """Standard tier: 1000-char p99 > 20ms but ≤ 30ms."""
        measurements = {200: 8.0, 500: 18.0, 1000: 25.0}
        result = derive_thresholds(measurements)

        assert result.tier == "standard"
        assert result.sync_cap == 1000

    def test_standard_tier_at_boundary_30ms(self) -> None:
        """Standard tier boundary: 1000-char p99 = 30ms exactly."""
        measurements = {200: 10.0, 500: 20.0, 1000: 30.0}
        result = derive_thresholds(measurements)

        assert result.tier == "standard"
        assert result.sync_cap == 1000

    def test_standard_tier_timeout_derivation(self) -> None:
        """Standard tier: timeout = p99_at_1000 × 1.5, bounded [25ms, 60ms]."""
        measurements = {200: 10.0, 500: 20.0, 1000: 28.0}
        result = derive_thresholds(measurements)

        expected_timeout_s = 0.028 * 1.5  # = 0.042s
        assert result.timeout_s == pytest.approx(expected_timeout_s, rel=1e-3)
        assert result.timeout_s >= PRESIDIO_TIMEOUT_MIN_S
        assert result.timeout_s <= PRESIDIO_TIMEOUT_MAX_S

    # ── Tier: slow ──────────────────────────────────────────────────────────

    def test_slow_tier_500_fast_1000_slow(self) -> None:
        """Slow tier: 500-char p99 ≤ 30ms but 1000-char p99 > 30ms."""
        measurements = {200: 12.0, 500: 22.0, 1000: 45.0}
        result = derive_thresholds(measurements)

        assert result.tier == "slow"
        assert result.sync_cap == 500

    def test_slow_tier_timeout_based_on_500_char_p99(self) -> None:
        """Slow tier: timeout is derived from 500-char p99 (the sync_cap size)."""
        measurements = {200: 12.0, 500: 22.0, 1000: 45.0}
        result = derive_thresholds(measurements)

        expected_timeout_s = 0.022 * 1.5  # = 0.033s
        assert result.timeout_s == pytest.approx(expected_timeout_s, rel=1e-3)

    def test_slow_tier_just_above_30ms_boundary(self) -> None:
        """1000-char p99 just above 30ms → slow tier, sync_cap=500."""
        measurements = {200: 10.0, 500: 25.0, 1000: 30.1}
        result = derive_thresholds(measurements)

        assert result.tier == "slow"
        assert result.sync_cap == 500

    # ── Tier: minimal ────────────────────────────────────────────────────────

    def test_minimal_tier_200_char_exceeds_target(self) -> None:
        """Minimal tier: even 200-char p99 > 30ms → sync_cap=0, advisory-only."""
        measurements = {200: 35.0, 500: 80.0, 1000: 150.0}
        result = derive_thresholds(measurements)

        assert result.tier == "minimal"
        assert result.sync_cap == 0

    def test_minimal_tier_timeout_based_on_200_char_p99(self) -> None:
        """Minimal tier: timeout derived from smallest measured size (200 chars)."""
        measurements = {200: 35.0, 500: 80.0, 1000: 150.0}
        result = derive_thresholds(measurements)

        # 35ms × 1.5 = 52.5ms → 0.0525s, within [25ms, 60ms]
        expected_timeout_s = 0.035 * 1.5  # = 0.0525
        assert result.timeout_s == pytest.approx(expected_timeout_s, rel=1e-3)

    def test_minimal_tier_timeout_capped_at_60ms(self) -> None:
        """Minimal tier: timeout capped at 60ms maximum."""
        measurements = {200: 50.0, 500: 100.0, 1000: 200.0}
        result = derive_thresholds(measurements)

        # 50ms × 1.5 = 75ms → capped at 60ms
        assert result.timeout_s == pytest.approx(PRESIDIO_TIMEOUT_MAX_S)

    # ── Edge cases ───────────────────────────────────────────────────────────

    def test_empty_measurements_returns_max_timeout(self) -> None:
        """Empty measurements → sync_cap=0, timeout=max (no data = most conservative)."""
        result = derive_thresholds({})

        assert result.sync_cap == 0
        assert result.timeout_s == pytest.approx(PRESIDIO_TIMEOUT_MAX_S)

    def test_only_200_char_measured_fast(self) -> None:
        """Only 200-char measured and fast → sync_cap=200, tier reflects missing 1000."""
        measurements = {200: 10.0}  # Only smallest measured
        result = derive_thresholds(measurements)

        assert result.sync_cap == 200
        # tier should be "slow" (sync_cap < 1000, 1000 not measured)
        assert result.tier == "slow"

    def test_measurements_preserved_in_result(self) -> None:
        """derive_thresholds preserves measurements dict in CalibrationResult."""
        measurements = {200: 8.0, 500: 18.0, 1000: 28.0}
        result = derive_thresholds(measurements)

        assert result.measurements == measurements

    def test_exactly_at_all_thresholds(self) -> None:
        """Verify boundary: all measurements exactly at 30ms → standard, sync_cap=1000."""
        measurements = {200: 30.0, 500: 30.0, 1000: 30.0}
        result = derive_thresholds(measurements)

        assert result.sync_cap == 1000
        assert result.tier == "standard"

    def test_just_above_target_for_all_sizes(self) -> None:
        """All sizes just above 30ms → sync_cap=0, minimal."""
        measurements = {200: 30.1, 500: 30.1, 1000: 30.1}
        result = derive_thresholds(measurements)

        assert result.sync_cap == 0
        assert result.tier == "minimal"

    def test_timeout_minimum_floor_applied(self) -> None:
        """Timeout never goes below PRESIDIO_TIMEOUT_MIN_S regardless of latency."""
        # Very fast hardware: 5ms p99 × 1.5 = 7.5ms < 25ms floor
        measurements = {200: 2.0, 500: 4.0, 1000: 5.0}
        result = derive_thresholds(measurements)

        assert result.timeout_s >= PRESIDIO_TIMEOUT_MIN_S

    def test_sync_cap_takes_largest_eligible_size(self) -> None:
        """sync_cap is the LARGEST size where p99 ≤ target (not the smallest)."""
        measurements = {200: 5.0, 500: 25.0, 1000: 29.0}  # All ≤ 30ms
        result = derive_thresholds(measurements)

        # Should take 1000, not 200 or 500
        assert result.sync_cap == 1000


# ─── CalibrationResult.conservative_fallback correctness ─────────────────────


class TestConservativeFallback:
    """Tests for the conservative fallback used when calibration fails."""

    def test_fallback_sync_cap_is_default(self) -> None:
        """Fallback sync_cap matches DEFAULT_PRESIDIO_SYNC_CAP constant."""
        result = CalibrationResult.conservative_fallback("test reason")
        assert result.sync_cap == DEFAULT_PRESIDIO_SYNC_CAP

    def test_fallback_timeout_is_maximum(self) -> None:
        """Fallback timeout equals the ceiling (PRESIDIO_TIMEOUT_FALLBACK_S)."""
        result = CalibrationResult.conservative_fallback("test reason")
        assert result.timeout_s == pytest.approx(PRESIDIO_TIMEOUT_FALLBACK_S)

    def test_fallback_tier_is_minimal(self) -> None:
        """Fallback tier is 'minimal' — worst-case assumption."""
        result = CalibrationResult.conservative_fallback("test reason")
        assert result.tier == "minimal"

    def test_fallback_calibration_ok_is_false(self) -> None:
        """Fallback sets calibration_ok=False."""
        result = CalibrationResult.conservative_fallback("reason here")
        assert result.calibration_ok is False

    def test_fallback_preserves_reason(self) -> None:
        """Fallback preserves the provided fallback_reason string."""
        reason = "ProcessPoolExecutor failed to start"
        result = CalibrationResult.conservative_fallback(reason)
        assert result.fallback_reason == reason

    def test_fallback_measurements_empty(self) -> None:
        """Fallback measurements dict is empty (no successful probes)."""
        result = CalibrationResult.conservative_fallback("test")
        assert result.measurements == {}


# ─── _make_calibration_text ───────────────────────────────────────────────────


class TestMakeCalibrationText:
    """Tests for the calibration text generator."""

    def test_length_200(self) -> None:
        """_make_calibration_text(200) returns exactly 200 characters."""
        text = _make_calibration_text(200)
        assert len(text) == 200

    def test_length_500(self) -> None:
        """_make_calibration_text(500) returns exactly 500 characters."""
        text = _make_calibration_text(500)
        assert len(text) == 500

    def test_length_1000(self) -> None:
        """_make_calibration_text(1000) returns exactly 1000 characters."""
        text = _make_calibration_text(1000)
        assert len(text) == 1000

    def test_all_calibration_sizes(self) -> None:
        """_make_calibration_text produces correct length for all calibration sizes."""
        for size in PRESIDIO_CALIBRATION_SIZES:
            text = _make_calibration_text(size)
            assert len(text) == size, f"Expected {size} chars, got {len(text)}"

    def test_contains_no_obvious_pii(self) -> None:
        """Calibration text should not contain real PII patterns (SSN, CC, email)."""
        import re
        text = _make_calibration_text(1000)
        # No SSN pattern (xxx-xx-xxxx with digits)
        assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b", text)
        # No obvious credit card pattern
        assert not re.search(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b", text)
        # No email addresses (no @ signs)
        assert "@" not in text

    def test_text_is_string(self) -> None:
        """_make_calibration_text returns a str, not bytes."""
        text = _make_calibration_text(200)
        assert isinstance(text, str)

    def test_text_is_ascii(self) -> None:
        """Calibration text is ASCII-only (no Unicode surprises in Presidio)."""
        text = _make_calibration_text(500)
        assert text.isascii()


# ─── run_calibration (async, mocked pool) ────────────────────────────────────


class TestRunCalibration:
    """Integration tests for run_calibration() with mocked ProcessPoolExecutor."""

    @pytest.mark.asyncio
    async def test_returns_calibration_result(self) -> None:
        """run_calibration() returns a CalibrationResult instance."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)

        # Mock the async loop.run_in_executor to return near-instantly
        with patch("asyncio.get_event_loop") as mock_loop_factory:
            mock_loop = MagicMock()
            mock_loop_factory.return_value = mock_loop

            # Create a coroutine that returns None (simulating fast Presidio call)
            async def fast_scan(*args):
                return None

            mock_loop.run_in_executor = lambda pool, fn, text: fast_scan()

            result = await run_calibration(mock_pool)

        assert isinstance(result, CalibrationResult)

    @pytest.mark.asyncio
    async def test_exception_returns_conservative_fallback(self) -> None:
        """run_calibration() returns conservative fallback on exception."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)

        # Simulate an exception during calibration
        with patch("asyncio.get_event_loop") as mock_loop_factory:
            mock_loop = MagicMock()
            mock_loop_factory.return_value = mock_loop
            mock_loop.run_in_executor.side_effect = RuntimeError("pool crashed")

            result = await run_calibration(mock_pool)

        assert result.calibration_ok is False
        assert result.fallback_reason is not None
        assert "RuntimeError" in result.fallback_reason
        assert result.sync_cap == DEFAULT_PRESIDIO_SYNC_CAP
        assert result.timeout_s == pytest.approx(PRESIDIO_TIMEOUT_FALLBACK_S)

    @pytest.mark.asyncio
    async def test_calibration_produces_valid_tier(self) -> None:
        """run_calibration() result always has a valid tier string."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)

        with patch("asyncio.get_event_loop") as mock_loop_factory:
            mock_loop = MagicMock()
            mock_loop_factory.return_value = mock_loop

            async def fast_scan(*args):
                return None

            mock_loop.run_in_executor = lambda pool, fn, text: fast_scan()
            result = await run_calibration(mock_pool)

        assert result.tier in ("fast", "standard", "slow", "minimal")

    @pytest.mark.asyncio
    async def test_timeout_per_call_does_not_block_startup(self) -> None:
        """Calibration times out gracefully and does not block indefinitely."""
        import asyncio as aio

        mock_pool = MagicMock(spec=ProcessPoolExecutor)

        with patch("asyncio.get_event_loop") as mock_loop_factory:
            mock_loop = MagicMock()
            mock_loop_factory.return_value = mock_loop

            # Simulate slow but not infinite execution
            async def slow_scan(*args):
                await aio.sleep(0.001)  # 1ms — fast enough for tests
                return None

            mock_loop.run_in_executor = lambda pool, fn, text: slow_scan()

            result = await run_calibration(mock_pool)

        # Should complete and return some kind of result
        assert isinstance(result, CalibrationResult)


# ─── Tier classification exhaustive cases ─────────────────────────────────────


class TestTierClassification:
    """Exhaustive tier classification test matrix."""

    @pytest.mark.parametrize("p99_1000", [1.0, 5.0, 10.0, 15.0, 20.0])
    def test_fast_tier_for_p99_up_to_20ms(self, p99_1000: float) -> None:
        """Fast tier for any 1000-char p99 ≤ 20ms."""
        measurements = {200: p99_1000 / 4, 500: p99_1000 / 2, 1000: p99_1000}
        result = derive_thresholds(measurements)
        assert result.tier == "fast"
        assert result.sync_cap == 1000

    @pytest.mark.parametrize("p99_1000", [21.0, 25.0, 28.0, 30.0])
    def test_standard_tier_for_p99_21_to_30ms(self, p99_1000: float) -> None:
        """Standard tier for 1000-char p99 > 20ms and ≤ 30ms."""
        measurements = {200: p99_1000 / 4, 500: p99_1000 / 2, 1000: p99_1000}
        result = derive_thresholds(measurements)
        assert result.tier == "standard"
        assert result.sync_cap == 1000

    @pytest.mark.parametrize("p99_500", [20.0, 25.0, 29.9, 30.0])
    def test_slow_tier_sync_cap_500(self, p99_500: float) -> None:
        """Slow tier: 500-char p99 ≤ 30ms, 1000-char p99 > 30ms."""
        measurements = {200: p99_500 / 2, 500: p99_500, 1000: 45.0}
        result = derive_thresholds(measurements)
        assert result.tier == "slow"
        assert result.sync_cap == 500

    @pytest.mark.parametrize("p99_200", [31.0, 40.0, 50.0, 100.0])
    def test_minimal_tier_sync_cap_zero(self, p99_200: float) -> None:
        """Minimal tier: even 200-char p99 > 30ms → sync_cap=0."""
        measurements = {200: p99_200, 500: p99_200 * 2, 1000: p99_200 * 4}
        result = derive_thresholds(measurements)
        assert result.tier == "minimal"
        assert result.sync_cap == 0
