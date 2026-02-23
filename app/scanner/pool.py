"""Presidio ProcessPoolExecutor management — E-003-S-003, E-003-S-004.

Provides:
  - startup_scan_pool(): Creates ProcessPoolExecutor, runs smoke test + calibration.
  - shutdown_scan_pool(): Graceful pool shutdown.
  - get_scan_pool(): FastAPI dependency — returns live pool or HTTP 503.

Architecture reference: architecture.md §3, §13 (Adaptive Performance Protocol)
Stories: E-003-S-003 (pool creation + calibration), E-003-S-004 (DI + shutdown)

Non-negotiables:
  - ProcessPoolExecutor(initializer=presidio_worker_init, ...) — MANDATORY PATTERN
  - startup_scan_pool() calls run_calibration(pool) from calibration.py — NO duplication
  - Never raises — failures return conservative fallback
  - Returns (pool, CalibrationResult) tuple always
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from typing import Optional, Tuple

from fastapi import HTTPException, Request

from app.scanner.calibration import CalibrationResult, run_calibration
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Generous timeout for smoke test — accounts for spaCy model loading on slow machines.
# The workers have already run presidio_worker_init() by this point (model loaded),
# but IPC round-trip + first pickle can be slow on budget hardware.
_SMOKE_TEST_TIMEOUT_S: float = 30.0


async def _run_smoke_test(pool: ProcessPoolExecutor, scan_fn) -> None:
    """Run a smoke test scan through the pool to verify the worker is alive.

    Extracted as a separate function to make startup_scan_pool() more testable.

    Args:
        pool:    The ProcessPoolExecutor to test.
        scan_fn: The scan worker function (presidio_scan_worker).

    Raises:
        Any exception from the scan (timeout, RuntimeError, etc.) — caller handles it.
    """
    loop = asyncio.get_event_loop()
    await asyncio.wait_for(
        loop.run_in_executor(pool, scan_fn, "smoke test"),
        timeout=_SMOKE_TEST_TIMEOUT_S,
    )


async def startup_scan_pool(
    config: object,
) -> Tuple[Optional[ProcessPoolExecutor], CalibrationResult]:
    """Create Presidio ProcessPoolExecutor, run smoke test, then run calibration.

    Called by main.py lifespan BEFORE app.state.ready = True.
    Never raises — on any failure, returns conservative fallback.
    Calibration is delegated to calibration.py's run_calibration() — no duplication.

    Startup sequence:
      0. If scanner.mode == "lite", skip pool entirely → return (None, fallback)
      1. Build entity set from config (respecting enable_person_detection flag)
      2. Create ProcessPoolExecutor(max_workers=1, initializer=presidio_worker_init)
      3. Run smoke test scan to verify worker is alive and warmed up
      4. Call run_calibration(pool) to measure hardware latency
      5. Return (pool, calibration_result)

    On pool creation failure: returns (None, conservative_fallback)
    On smoke test failure:    returns (None, conservative_fallback), pool shutdown
    On calibration failure:   returns (pool, conservative_fallback) — pool still usable

    Args:
        config: Application Config object (Config dataclass from app.config).

    Returns:
        Tuple of:
          - ProcessPoolExecutor (or None on failure)
          - CalibrationResult with hardware-derived sync_cap and timeout_s
    """
    # ── Step 0: Lite mode — skip Presidio pool entirely ───────────────────────
    # In lite mode the scan engine uses the regex fast-path only (scan_pool=None).
    # No ProcessPoolExecutor, no spaCy model load, no calibration needed.
    scanner_config = getattr(config, "scanner", None)
    if getattr(scanner_config, "mode", "full") == "lite":
        logger.info(
            "Scanner mode is 'lite' — skipping Presidio pool startup (regex-only)"
        )
        return None, CalibrationResult.conservative_fallback(
            reason="Lite mode: Presidio pool intentionally skipped"
        )

    # Lazy import to avoid circular dependency at module load time
    from app.scanner.presidio_worker import presidio_scan_worker, presidio_worker_init

    # ── Step 1: Build entity set ──────────────────────────────────────────────
    # scanner_config already resolved in Step 0 above.
    base_entity_set: list[str] = list(
        getattr(scanner_config, "entity_set", [
            "CREDIT_CARD", "CRYPTO", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN"
        ])
    )

    # PERSON is opt-in via enable_person_detection (default: excluded)
    if getattr(scanner_config, "enable_person_detection", False):
        if "PERSON" not in base_entity_set:
            base_entity_set.append("PERSON")

    entity_tuple: tuple[str, ...] = tuple(base_entity_set)
    logger.info(
        "Entity set configured",
        entity_set=list(entity_tuple),
    )

    # ── Step 2: Create pool ───────────────────────────────────────────────────
    # MANDATORY PATTERN: initializer=presidio_worker_init
    # CI lint gate confirms every ProcessPoolExecutor in this file uses initializer=
    logger.info("Creating Presidio scan pool (max_workers=1)")
    try:
        pool = ProcessPoolExecutor(
            max_workers=1,
            initializer=presidio_worker_init,
            initargs=(entity_tuple,),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to create Presidio scan pool",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None, CalibrationResult.conservative_fallback(
            reason=f"Pool creation failed: {type(exc).__name__}: {exc}"
        )

    # ── Step 3: Smoke test ────────────────────────────────────────────────────
    # Verifies the worker process is alive and presidio_worker_init() completed.
    try:
        await _run_smoke_test(pool, presidio_scan_worker)
        logger.info("Presidio scan pool smoke test passed")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Presidio scan pool smoke test failed — pool unavailable",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        # Shut down the broken pool
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass
        return None, CalibrationResult.conservative_fallback(
            reason=f"Smoke test failed: {type(exc).__name__}: {exc}"
        )

    # ── Step 4: Calibration ───────────────────────────────────────────────────
    # Delegates entirely to calibration.py's run_calibration() — no logic here.
    # run_calibration() probes 3 sizes × 5 iterations and derives sync_cap/timeout_s.
    logger.info("Running Presidio hardware calibration...")
    try:
        calibration = await run_calibration(pool)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Calibration failed — using conservative fallback (pool still available)",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        calibration = CalibrationResult.conservative_fallback(
            reason=f"Calibration exception: {type(exc).__name__}: {exc}"
        )

    logger.info(
        "Presidio scan pool ready",
        tier=calibration.tier,
        sync_cap=calibration.sync_cap,
        timeout_ms=round(calibration.timeout_ms, 1),
    )
    return pool, calibration


async def shutdown_scan_pool(scan_pool: Optional[ProcessPoolExecutor]) -> None:
    """Graceful Presidio scan pool shutdown.

    Called by main.py lifespan during application shutdown.

    Args:
        scan_pool: The ProcessPoolExecutor to shut down (or None — no-op).
    """
    if scan_pool is None:
        logger.debug("Scan pool shutdown: no pool to shut down")
        return
    logger.info("Shutting down Presidio scan pool...")
    scan_pool.shutdown(wait=True, cancel_futures=False)
    logger.info("Presidio scan pool shutdown complete")


# ── FastAPI Dependency ────────────────────────────────────────────────────────


async def get_scan_pool(request: Request) -> ProcessPoolExecutor:
    """FastAPI dependency — returns the live scan pool or raises HTTP 503.

    Used as Depends(get_scan_pool) in route handlers that need the pool.
    The require_ready dependency in main.py is the primary guard; this is
    defense-in-depth for edge cases where a request arrives before pool init.

    Args:
        request: FastAPI Request (injected by dependency system).

    Returns:
        Live ProcessPoolExecutor from app.state.

    Raises:
        HTTPException(503): If app.state.scan_pool is None (scanner not ready).
    """
    pool: Optional[ProcessPoolExecutor] = getattr(request.app.state, "scan_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "starting",
                "scanner": "initializing",
                "message": "Scanner not ready",
            },
        )
    return pool
