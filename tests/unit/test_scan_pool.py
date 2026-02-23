"""Tests for startup_scan_pool(), shutdown_scan_pool(), get_scan_pool() — E-003-S-003, E-003-S-004."""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.scanner.calibration import CalibrationResult
from app.scanner.pool import get_scan_pool, shutdown_scan_pool, startup_scan_pool


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_conservative_fallback(reason: str = "test fallback") -> CalibrationResult:
    return CalibrationResult.conservative_fallback(reason=reason)


def _make_config(entity_set=None, enable_person_detection=False):
    config = MagicMock()
    config.scanner.entity_set = entity_set or [
        "CREDIT_CARD", "CRYPTO", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN"
    ]
    config.scanner.enable_person_detection = enable_person_detection
    return config


# ─── E-003-S-003: startup_scan_pool() ────────────────────────────────────────


class TestStartupScanPool:
    @pytest.mark.asyncio
    async def test_returns_pool_and_calibration_result(self):
        """startup_scan_pool() returns (pool, CalibrationResult) on success."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)
        mock_calibration = CalibrationResult(
            sync_cap=1000, timeout_s=0.045, tier="standard", calibration_ok=True
        )

        with patch("app.scanner.pool.ProcessPoolExecutor", return_value=mock_pool), \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(return_value=mock_calibration)), \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)):
            pool, cal = await startup_scan_pool(_make_config())

        assert pool is mock_pool
        assert cal is mock_calibration

    @pytest.mark.asyncio
    async def test_uses_initializer_with_presidio_worker_init(self):
        """ProcessPoolExecutor is created with initializer=presidio_worker_init."""
        from app.scanner.presidio_worker import presidio_worker_init

        with patch("app.scanner.pool.ProcessPoolExecutor") as mock_ppe, \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(return_value=_make_conservative_fallback())), \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)):
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            mock_ppe.return_value = mock_pool

            await startup_scan_pool(_make_config())

        call_kwargs = mock_ppe.call_args[1]
        assert call_kwargs.get("initializer") is presidio_worker_init
        assert call_kwargs.get("max_workers") == 1

    @pytest.mark.asyncio
    async def test_initargs_contains_entity_tuple(self):
        """initargs=(entity_tuple,) is passed to ProcessPoolExecutor."""
        with patch("app.scanner.pool.ProcessPoolExecutor") as mock_ppe, \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(return_value=_make_conservative_fallback())), \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)):
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            mock_ppe.return_value = mock_pool

            await startup_scan_pool(_make_config())

        call_kwargs = mock_ppe.call_args[1]
        initargs = call_kwargs.get("initargs", ())
        assert len(initargs) == 1
        assert isinstance(initargs[0], tuple)  # entity_set is a tuple

    @pytest.mark.asyncio
    async def test_pool_creation_failure_returns_none_and_fallback(self):
        """Pool creation failure → (None, conservative_fallback)."""
        with patch("app.scanner.pool.ProcessPoolExecutor", side_effect=OSError("cannot fork")):
            pool, cal = await startup_scan_pool(_make_config())

        assert pool is None
        assert cal.calibration_ok is False
        assert "Pool creation failed" in (cal.fallback_reason or "")

    @pytest.mark.asyncio
    async def test_smoke_test_failure_returns_none_and_fallback(self):
        """Smoke test failure → (None, conservative_fallback), pool.shutdown called."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)

        with patch("app.scanner.pool.ProcessPoolExecutor", return_value=mock_pool), \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(side_effect=RuntimeError("worker dead"))):
            pool, cal = await startup_scan_pool(_make_config())

        assert pool is None
        assert cal.calibration_ok is False
        assert "Smoke test failed" in (cal.fallback_reason or "")
        mock_pool.shutdown.assert_called()

    @pytest.mark.asyncio
    async def test_calibration_failure_returns_pool_and_fallback(self):
        """Calibration failure → (pool, conservative_fallback) — pool still available!"""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)

        with patch("app.scanner.pool.ProcessPoolExecutor", return_value=mock_pool), \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)), \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(side_effect=Exception("calibration error"))):
            pool, cal = await startup_scan_pool(_make_config())

        # Pool is still returned even when calibration fails!
        assert pool is mock_pool
        assert cal.calibration_ok is False

    @pytest.mark.asyncio
    async def test_calls_run_calibration_after_smoke_test(self):
        """run_calibration(pool) is called after smoke test succeeds."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)
        mock_calibration = _make_conservative_fallback()
        mock_run_cal = AsyncMock(return_value=mock_calibration)

        with patch("app.scanner.pool.ProcessPoolExecutor", return_value=mock_pool), \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)), \
             patch("app.scanner.pool.run_calibration", new=mock_run_cal):
            await startup_scan_pool(_make_config())

        mock_run_cal.assert_called_once_with(mock_pool)

    @pytest.mark.asyncio
    async def test_person_excluded_by_default(self):
        """PERSON not in entity_set when enable_person_detection=False."""
        with patch("app.scanner.pool.ProcessPoolExecutor") as mock_ppe, \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)), \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(return_value=_make_conservative_fallback())):
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            mock_ppe.return_value = mock_pool

            await startup_scan_pool(_make_config(enable_person_detection=False))

        initargs = mock_ppe.call_args[1].get("initargs", ())
        entity_tuple = initargs[0] if initargs else ()
        assert "PERSON" not in entity_tuple

    @pytest.mark.asyncio
    async def test_person_included_when_enabled(self):
        """PERSON added to entity_set when enable_person_detection=True."""
        with patch("app.scanner.pool.ProcessPoolExecutor") as mock_ppe, \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)), \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(return_value=_make_conservative_fallback())):
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            mock_ppe.return_value = mock_pool

            await startup_scan_pool(_make_config(enable_person_detection=True))

        initargs = mock_ppe.call_args[1].get("initargs", ())
        entity_tuple = initargs[0] if initargs else ()
        assert "PERSON" in entity_tuple

    @pytest.mark.asyncio
    async def test_never_raises_on_catastrophic_failure(self):
        """startup_scan_pool() NEVER raises — even with catastrophic failure."""
        with patch("app.scanner.pool.ProcessPoolExecutor", side_effect=MemoryError("OOM")):
            result = await startup_scan_pool(_make_config())

        pool, cal = result
        assert pool is None
        assert isinstance(cal, CalibrationResult)

    @pytest.mark.asyncio
    async def test_smoke_test_timeout_returns_none_fallback(self):
        """Smoke test asyncio.TimeoutError → (None, conservative_fallback)."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)

        with patch("app.scanner.pool.ProcessPoolExecutor", return_value=mock_pool), \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            pool, cal = await startup_scan_pool(_make_config())

        assert pool is None
        assert cal.calibration_ok is False


# ─── E-003-S-004: shutdown_scan_pool() ───────────────────────────────────────


class TestShutdownScanPool:
    @pytest.mark.asyncio
    async def test_shutdown_none_is_noop(self):
        """shutdown_scan_pool(None) is a no-op (no exception)."""
        await shutdown_scan_pool(None)  # Should not raise

    @pytest.mark.asyncio
    async def test_shutdown_calls_pool_shutdown(self):
        """shutdown_scan_pool(pool) calls pool.shutdown(wait=True, cancel_futures=False)."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)
        await shutdown_scan_pool(mock_pool)
        mock_pool.shutdown.assert_called_once_with(wait=True, cancel_futures=False)


# ─── E-003-S-004: get_scan_pool() dependency ─────────────────────────────────


class TestGetScanPool:
    @pytest.mark.asyncio
    async def test_returns_503_when_pool_is_none(self):
        """get_scan_pool() raises HTTPException(503) when app.state.scan_pool is None."""
        request = MagicMock()
        request.app.state.scan_pool = None

        with pytest.raises(HTTPException) as exc_info:
            await get_scan_pool(request)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["status"] == "starting"

    @pytest.mark.asyncio
    async def test_returns_pool_when_available(self):
        """get_scan_pool() returns the pool when app.state.scan_pool is set."""
        mock_pool = MagicMock(spec=ProcessPoolExecutor)
        request = MagicMock()
        request.app.state.scan_pool = mock_pool

        result = await get_scan_pool(request)

        assert result is mock_pool

    @pytest.mark.asyncio
    async def test_503_body_has_required_fields(self):
        """503 response body has status, scanner, message fields."""
        request = MagicMock()
        request.app.state.scan_pool = None

        with pytest.raises(HTTPException) as exc_info:
            await get_scan_pool(request)

        detail = exc_info.value.detail
        assert "status" in detail
        assert "scanner" in detail
        assert "message" in detail
        assert detail["scanner"] == "initializing"

    @pytest.mark.asyncio
    async def test_returns_503_when_getattr_returns_none(self):
        """get_scan_pool() raises 503 when state has no scan_pool."""
        request = MagicMock(spec=["app"])
        request.app = MagicMock()
        request.app.state = MagicMock()
        request.app.state.scan_pool = None

        with pytest.raises(HTTPException) as exc_info:
            await get_scan_pool(request)

        assert exc_info.value.status_code == 503


# ─── Lite Mode ────────────────────────────────────────────────────────────────


def _make_lite_config():
    """Config with scanner.mode = 'lite'."""
    config = MagicMock()
    config.scanner.mode = "lite"
    config.scanner.entity_set = ["CREDIT_CARD", "EMAIL_ADDRESS"]
    config.scanner.enable_person_detection = False
    return config


def _make_full_config():
    """Config with scanner.mode = 'full'."""
    config = MagicMock()
    config.scanner.mode = "full"
    config.scanner.entity_set = ["CREDIT_CARD", "EMAIL_ADDRESS"]
    config.scanner.enable_person_detection = False
    return config


class TestLiteMode:
    """startup_scan_pool() must short-circuit when scanner.mode == 'lite'."""

    @pytest.mark.asyncio
    async def test_lite_mode_returns_none_pool(self):
        """In lite mode, startup_scan_pool() must return None as the pool (regex-only)."""
        config = _make_lite_config()
        pool, _ = await startup_scan_pool(config)
        assert pool is None, (
            "Lite mode must return None pool — Presidio must not be started."
        )

    @pytest.mark.asyncio
    async def test_lite_mode_returns_calibration_result(self):
        """In lite mode, a CalibrationResult fallback is still returned (not None)."""
        config = _make_lite_config()
        _, calibration = await startup_scan_pool(config)
        assert isinstance(calibration, CalibrationResult), (
            "startup_scan_pool() must always return a CalibrationResult, even in lite mode."
        )

    @pytest.mark.asyncio
    async def test_lite_mode_never_calls_presidio_worker_init(self):
        """In lite mode, presidio_worker_init must never be imported or called."""
        config = _make_lite_config()
        with patch("app.scanner.pool.ProcessPoolExecutor") as mock_ppe:
            await startup_scan_pool(config)
            mock_ppe.assert_not_called(), (
                "ProcessPoolExecutor must not be created in lite mode."
            )

    @pytest.mark.asyncio
    async def test_full_mode_still_attempts_pool_creation(self):
        """In full mode, startup_scan_pool() still attempts to create the Presidio pool."""
        config = _make_full_config()
        with patch("app.scanner.pool.ProcessPoolExecutor", side_effect=RuntimeError("no presidio")) as mock_ppe:
            with patch("app.scanner.presidio_worker.presidio_worker_init"):
                pool, calibration = await startup_scan_pool(config)
                mock_ppe.assert_called_once(), (
                    "Full mode must attempt ProcessPoolExecutor creation."
                )
        # Pool creation failed → should return None with conservative fallback
        assert pool is None
        assert isinstance(calibration, CalibrationResult)

    @pytest.mark.asyncio
    async def test_lite_mode_missing_scanner_config_defaults_to_full(self):
        """If config has no scanner attribute, mode defaults to 'full' (not lite)."""
        config = MagicMock()
        config.scanner = None  # no scanner config at all
        with patch("app.scanner.pool.ProcessPoolExecutor", side_effect=RuntimeError("no presidio")):
            with patch("app.scanner.presidio_worker.presidio_worker_init"):
                pool, calibration = await startup_scan_pool(config)
        # Should have tried full mode (pool creation attempted → failed → None)
        assert pool is None
        assert isinstance(calibration, CalibrationResult)
