"""Unit tests for app/constants.py — shared size constants (E-001-S-005).

Verifies that all size constants are defined with the correct values in the
shared constants module (no magic numbers in other modules).

Updated for HOLD-002 fix: PRESIDIO_SYNC_CAP renamed to DEFAULT_PRESIDIO_SYNC_CAP
(conservative fallback) and new calibration constants added.

Architecture reference: architecture.md §9.3, §13 (Adaptive Performance Protocol)
Story: E-001-S-005; HOLD-002 fix
"""

from __future__ import annotations

from app.constants import (
    DEFAULT_PRESIDIO_SYNC_CAP,
    INPUT_HARD_CAP,
    MAX_REQUEST_BODY_BYTES,
    MAX_RESPONSE_BUFFER_BYTES,
    PRESIDIO_CALIBRATION_ITERATIONS,
    PRESIDIO_CALIBRATION_SIZES,
    PRESIDIO_TARGET_LATENCY_MS,
    PRESIDIO_TIMEOUT_FALLBACK_S,
    PRESIDIO_TIMEOUT_MAX_S,
    PRESIDIO_TIMEOUT_MIN_S,
    PRESIDIO_TIMEOUT_MULTIPLIER,
)


class TestSizeConstants:
    """Verify all four named constants have their specified values."""

    def test_max_request_body_bytes_is_1mb(self) -> None:
        """MAX_REQUEST_BODY_BYTES must be exactly 1 MB (1,048,576 bytes)."""
        assert MAX_REQUEST_BODY_BYTES == 1_048_576

    def test_max_response_buffer_bytes_is_512kb(self) -> None:
        """MAX_RESPONSE_BUFFER_BYTES must be exactly 512 KB (524,288 bytes)."""
        assert MAX_RESPONSE_BUFFER_BYTES == 524_288

    def test_input_hard_cap_is_8192_chars(self) -> None:
        """INPUT_HARD_CAP must be 8,192 characters."""
        assert INPUT_HARD_CAP == 8_192

    def test_default_presidio_sync_cap_is_conservative(self) -> None:
        """DEFAULT_PRESIDIO_SYNC_CAP is the conservative fallback (500 chars).

        This is NOT the operational value — calibration derives the real value.
        500 chars is safe on any hardware; it is overridden upward on fast hardware.
        """
        assert DEFAULT_PRESIDIO_SYNC_CAP == 500

    def test_request_limit_greater_than_response_buffer(self) -> None:
        """1 MB request cap must be larger than 512 KB response buffer threshold."""
        assert MAX_REQUEST_BODY_BYTES > MAX_RESPONSE_BUFFER_BYTES

    def test_input_hard_cap_greater_than_default_presidio_sync_cap(self) -> None:
        """INPUT_HARD_CAP (8192) must be > DEFAULT_PRESIDIO_SYNC_CAP (500)."""
        assert INPUT_HARD_CAP > DEFAULT_PRESIDIO_SYNC_CAP

    def test_all_size_constants_are_positive(self) -> None:
        """All size constants must be positive integers."""
        for const in (
            MAX_REQUEST_BODY_BYTES,
            MAX_RESPONSE_BUFFER_BYTES,
            INPUT_HARD_CAP,
            DEFAULT_PRESIDIO_SYNC_CAP,
        ):
            assert const > 0, f"Expected positive value, got {const}"

    def test_all_size_constants_are_integers(self) -> None:
        """All size constants must be integers (not floats)."""
        for const in (
            MAX_REQUEST_BODY_BYTES,
            MAX_RESPONSE_BUFFER_BYTES,
            INPUT_HARD_CAP,
            DEFAULT_PRESIDIO_SYNC_CAP,
        ):
            assert isinstance(const, int), f"Expected int, got {type(const)}"


class TestCalibrationConstants:
    """Verify the Adaptive Performance Protocol calibration constants (HOLD-002 fix)."""

    def test_calibration_sizes_are_200_500_1000(self) -> None:
        """PRESIDIO_CALIBRATION_SIZES must include 200, 500, and 1000 chars."""
        assert 200 in PRESIDIO_CALIBRATION_SIZES
        assert 500 in PRESIDIO_CALIBRATION_SIZES
        assert 1000 in PRESIDIO_CALIBRATION_SIZES

    def test_calibration_sizes_ordered_smallest_first(self) -> None:
        """PRESIDIO_CALIBRATION_SIZES must be ordered smallest → largest."""
        sizes = list(PRESIDIO_CALIBRATION_SIZES)
        assert sizes == sorted(sizes)

    def test_calibration_iterations_is_5(self) -> None:
        """PRESIDIO_CALIBRATION_ITERATIONS must be 5."""
        assert PRESIDIO_CALIBRATION_ITERATIONS == 5

    def test_target_latency_is_30ms(self) -> None:
        """PRESIDIO_TARGET_LATENCY_MS must be 30.0ms (25% headroom below 40ms timeout)."""
        assert PRESIDIO_TARGET_LATENCY_MS == 30.0

    def test_timeout_multiplier_is_1_5(self) -> None:
        """PRESIDIO_TIMEOUT_MULTIPLIER must be 1.5 (50% buffer above measured p99)."""
        assert PRESIDIO_TIMEOUT_MULTIPLIER == 1.5

    def test_timeout_min_is_25ms(self) -> None:
        """PRESIDIO_TIMEOUT_MIN_S must be 0.025s (25ms floor)."""
        assert PRESIDIO_TIMEOUT_MIN_S == 0.025

    def test_timeout_max_is_60ms(self) -> None:
        """PRESIDIO_TIMEOUT_MAX_S must be 0.060s (60ms ceiling)."""
        assert PRESIDIO_TIMEOUT_MAX_S == 0.060

    def test_timeout_fallback_is_60ms(self) -> None:
        """PRESIDIO_TIMEOUT_FALLBACK_S must be 0.060s (most conservative)."""
        assert PRESIDIO_TIMEOUT_FALLBACK_S == 0.060

    def test_timeout_min_less_than_max(self) -> None:
        """Timeout floor must be less than ceiling."""
        assert PRESIDIO_TIMEOUT_MIN_S < PRESIDIO_TIMEOUT_MAX_S

    def test_timeout_fallback_equals_max(self) -> None:
        """Fallback timeout equals the max ceiling (most conservative)."""
        assert PRESIDIO_TIMEOUT_FALLBACK_S == PRESIDIO_TIMEOUT_MAX_S

    def test_all_sizes_greater_than_zero(self) -> None:
        """All calibration sizes must be positive."""
        for size in PRESIDIO_CALIBRATION_SIZES:
            assert size > 0


class TestConfigUsesConstants:
    """Verify that ScannerConfig defaults reference the shared constants."""

    def test_scanner_config_input_hard_cap_matches_constant(self) -> None:
        """ScannerConfig.input_hard_cap default must equal INPUT_HARD_CAP constant."""
        from app.config import ScannerConfig

        config = ScannerConfig()
        assert config.input_hard_cap == INPUT_HARD_CAP

    def test_scanner_config_presidio_sync_cap_matches_default_constant(self) -> None:
        """ScannerConfig.presidio_sync_cap default must equal DEFAULT_PRESIDIO_SYNC_CAP.

        This is the conservative fallback; at runtime, calibration may raise it
        to 500 or 1000 based on measured hardware performance.
        """
        from app.config import ScannerConfig

        config = ScannerConfig()
        assert config.presidio_sync_cap == DEFAULT_PRESIDIO_SYNC_CAP
