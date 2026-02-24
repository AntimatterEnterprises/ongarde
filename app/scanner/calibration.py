"""Adaptive Presidio performance calibration — HOLD-002 fix.

Implements the Adaptive Performance Protocol described in architecture.md §13.

At startup, BEFORE accepting traffic, this module probes actual Presidio latency
on the current hardware and sets PRESIDIO_SYNC_CAP + PRESIDIO_TIMEOUT_S dynamically.
This eliminates the hard-coded assumption that the host is a 2-vCPU DigitalOcean
Droplet and prevents false BLOCK events on slower hardware.

Algorithm (architecture.md §13.2):
  1. Scan 3 representative input sizes: 200, 500, 1000 chars (clean text, no PII)
  2. 5 iterations per size → measure p99 latency at each size
  3. PRESIDIO_SYNC_CAP = largest size where measured p99 ≤ 30ms
  4. PRESIDIO_TIMEOUT_S = measured_p99_at_sync_cap × 1.5 (clamped 25ms–60ms)

Hardware tiers (logged at startup, exposed in /health/scanner):
  "fast"     — 1000-char p99 ≤ 20ms
  "standard" — 1000-char p99 ≤ 30ms
  "slow"     — PRESIDIO_SYNC_CAP reduced to 500
  "minimal"  — PRESIDIO_SYNC_CAP = 0, advisory-only mode

Non-negotiables:
  - Calibration MUST complete before app.state.ready = True
  - Calibration failure MUST use conservative defaults (never crash startup)
  - No blocking I/O in the calibration path itself (uses run_in_executor)
  - Config overrides always win over calibration (checked in lifespan, not here)
  - Must complete in < 3 seconds total (5 iterations × 3 sizes)

Story: HOLD-002 fix — Adaptive Performance Protocol
Architecture: architecture.md §13
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from app.constants import (
    DEFAULT_PRESIDIO_SYNC_CAP,
    PRESIDIO_CALIBRATION_ITERATIONS,
    PRESIDIO_CALIBRATION_SIZES,
    PRESIDIO_TARGET_LATENCY_MS,
    PRESIDIO_TIMEOUT_FALLBACK_S,
    PRESIDIO_TIMEOUT_MAX_S,
    PRESIDIO_TIMEOUT_MIN_S,
    PRESIDIO_TIMEOUT_MULTIPLIER,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ─── Calibration text template ────────────────────────────────────────────────
# Clean text pattern with NO real PII. Varied enough to exercise the NLP pipeline
# realistically without triggering any detection rules.
_CALIBRATION_TEXT_TEMPLATE = (
    "The quick brown fox jumps over the lazy dog. "
    "Alice went to the market to buy fresh vegetables and fruits. "
    "Bob called his colleague to discuss the quarterly report. "
    "The conference is scheduled for next Tuesday in the main meeting room. "
    "Please review the attached document and provide your feedback by Friday. "
    "Our team is working on improving the user experience for the next release. "
    "The weather forecast shows sunny skies for the entire week ahead. "
    "She completed the training course and received her certification last month. "
    "The new policy will be effective from the first day of next month. "
    "Everyone is invited to the team lunch on Wednesday at noon in the cafeteria. "
)


def _make_calibration_text(size: int) -> str:
    """Generate clean calibration text of exactly ``size`` characters.

    Repeats the template as needed and truncates to the requested length.
    Contains no real PII — intentionally boring prose to probe raw NLP cost.

    Args:
        size: Target character count.

    Returns:
        String of exactly ``size`` characters.
    """
    template = _CALIBRATION_TEXT_TEMPLATE
    repetitions = (size // len(template)) + 2
    return (template * repetitions)[:size]


# ─── CalibrationResult ───────────────────────────────────────────────────────


@dataclass
class CalibrationResult:
    """Result of startup calibration. Stored in app.state.calibration.

    Fields:
        sync_cap:           Effective PRESIDIO_SYNC_CAP for this hardware (chars).
                            0 = advisory-only (Presidio disabled for sync path).
        timeout_s:          Effective PRESIDIO_TIMEOUT_S for this hardware (seconds).
        tier:               Hardware tier classification:
                              "fast"     — 1000-char p99 ≤ 20ms
                              "standard" — 1000-char p99 ≤ 30ms
                              "slow"     — sync_cap reduced to 500
                              "minimal"  — sync_cap = 0, advisory-only
        measurements:       Dict mapping size → measured p99_ms.
                            Keys are the sizes from PRESIDIO_CALIBRATION_SIZES.
                            Missing key = size was not measured (short-circuited).
        calibration_ok:     True if calibration completed successfully.
                            False if fallback defaults were used (pool unavailable, etc.)
        fallback_reason:    Human-readable reason for fallback (None if calibration_ok).
    """

    sync_cap: int
    timeout_s: float
    tier: str
    measurements: dict[int, float] = field(default_factory=dict)
    calibration_ok: bool = True
    fallback_reason: Optional[str] = None

    # ── Derived Properties ────────────────────────────────────────────────────

    @property
    def timeout_ms(self) -> float:
        """Effective timeout in milliseconds (for /health/scanner reporting)."""
        return self.timeout_s * 1000

    @property
    def measured_p99_at_sync_cap_ms(self) -> Optional[float]:
        """p99 latency (ms) at the effective sync_cap size, or None if not measured."""
        if self.sync_cap == 0:
            return self.measurements.get(PRESIDIO_CALIBRATION_SIZES[0])  # smallest size
        return self.measurements.get(self.sync_cap)

    # ── Factory: conservative fallback ───────────────────────────────────────

    @classmethod
    def conservative_fallback(cls, reason: str) -> "CalibrationResult":
        """Return conservative fallback result used when calibration fails.

        Fallback values (architecture.md §13.5):
          sync_cap = DEFAULT_PRESIDIO_SYNC_CAP (500 chars)
          timeout_s = PRESIDIO_TIMEOUT_FALLBACK_S (60ms)
          tier = "minimal" (worst-case assumption)

        These values are safe for any hardware — they simply mean fewer requests
        will use the Presidio sync path (more go advisory-only).
        """
        return cls(
            sync_cap=DEFAULT_PRESIDIO_SYNC_CAP,
            timeout_s=PRESIDIO_TIMEOUT_FALLBACK_S,
            tier="minimal",
            measurements={},
            calibration_ok=False,
            fallback_reason=reason,
        )


# ─── Threshold Derivation ─────────────────────────────────────────────────────


def derive_thresholds(
    measurements: dict[int, float],
    sizes: tuple[int, ...] = PRESIDIO_CALIBRATION_SIZES,
    target_ms: float = PRESIDIO_TARGET_LATENCY_MS,
    timeout_multiplier: float = PRESIDIO_TIMEOUT_MULTIPLIER,
    timeout_min_s: float = PRESIDIO_TIMEOUT_MIN_S,
    timeout_max_s: float = PRESIDIO_TIMEOUT_MAX_S,
) -> CalibrationResult:
    """Derive PRESIDIO_SYNC_CAP, PRESIDIO_TIMEOUT_S, and tier from measurements.

    This is a pure function — it takes measurements and returns thresholds.
    Separated from I/O so it can be unit-tested without a real pool.

    Algorithm (architecture.md §13.2):
      1. Find the largest size where measured p99 ≤ target_ms.
         If 200-char p99 > target_ms → sync_cap = 0 (minimal tier).
      2. timeout_s = p99_at_sync_cap × multiplier, clamped to [min, max].
         If sync_cap = 0: use p99 for the smallest measured size × multiplier.
      3. Tier classification based on 1000-char p99:
           fast:     1000-char p99 ≤ 20ms (well within budget)
           standard: 1000-char p99 ≤ 30ms (at target)
           slow:     1000-char p99 > 30ms but ≤ 60ms (sync_cap = 500)
           minimal:  sync_cap = 0 (advisory-only)

    Args:
        measurements:       Dict mapping size (int) → p99 latency (ms).
        sizes:              Ordered calibration sizes (smallest → largest).
        target_ms:          p99 threshold for sync eligibility (default 30ms).
        timeout_multiplier: Buffer multiplier applied to p99 (default 1.5).
        timeout_min_s:      Floor for derived timeout (default 0.025s).
        timeout_max_s:      Ceiling for derived timeout (default 0.060s).

    Returns:
        CalibrationResult with sync_cap, timeout_s, tier, and measurements set.
    """
    # ── Step 1: Find sync_cap ─────────────────────────────────────────────────
    sync_cap = 0
    for size in sorted(sizes):
        p99 = measurements.get(size)
        if p99 is None:
            # Size not measured — skip (short-circuited due to earlier failure)
            continue
        if p99 <= target_ms:
            sync_cap = size  # This size is fast enough; keep looking for larger ones

    # ── Step 2: Derive timeout ────────────────────────────────────────────────
    # Use p99 at the effective sync_cap size (or smallest if sync_cap = 0)
    if sync_cap > 0:
        reference_p99_ms = measurements.get(sync_cap)
    else:
        # Advisory-only mode: still set a reasonable timeout for any advisory probing
        reference_p99_ms = measurements.get(min(sizes))

    if reference_p99_ms is not None:
        timeout_s = (reference_p99_ms / 1000.0) * timeout_multiplier
        timeout_s = max(timeout_min_s, min(timeout_max_s, timeout_s))
    else:
        # No measurement at all — use maximum conservative timeout
        timeout_s = timeout_max_s

    # ── Step 3: Tier classification ───────────────────────────────────────────
    p99_1000 = measurements.get(1000)  # 1000-char p99 is the tier discriminator

    if sync_cap == 0:
        tier = "minimal"
    elif p99_1000 is None:
        # 1000-char not measured → tier based on what we do know
        tier = "slow" if sync_cap < 1000 else "standard"
    elif p99_1000 <= 20.0:
        tier = "fast"
    elif p99_1000 <= 30.0:
        tier = "standard"
    else:
        # p99_1000 > 30ms but sync_cap could still be 500 or 1000 at lower threshold
        # (shouldn't happen given algorithm, but be defensive)
        tier = "slow"

    return CalibrationResult(
        sync_cap=sync_cap,
        timeout_s=timeout_s,
        tier=tier,
        measurements=dict(measurements),
        calibration_ok=True,
        fallback_reason=None,
    )


# ─── Calibration Runner ───────────────────────────────────────────────────────


async def run_calibration(
    scan_pool: ProcessPoolExecutor,
) -> CalibrationResult:
    """Run Presidio calibration and return effective thresholds for this hardware.

    Probes the actual ProcessPoolExecutor with representative inputs to measure
    real Presidio latency on the current host CPU. Called BEFORE ready=True in
    the lifespan startup sequence.

    Timing guarantee: completes in < 3 seconds total on any hardware where Presidio
    responds (5 iterations × 3 sizes, with per-call timeout of 200ms).

    Args:
        scan_pool: The live, warmed ProcessPoolExecutor from startup_scan_pool().
                   Must already have run presidio_worker_init() warmup scans.

    Returns:
        CalibrationResult with hardware-appropriate sync_cap, timeout_s, and tier.
        On any exception: returns CalibrationResult.conservative_fallback().
    """
    loop = asyncio.get_event_loop()
    measurements: dict[int, float] = {}

    logger.info(
        "Presidio calibration starting",
        sizes=list(PRESIDIO_CALIBRATION_SIZES),
        iterations=PRESIDIO_CALIBRATION_ITERATIONS,
        target_ms=PRESIDIO_TARGET_LATENCY_MS,
    )

    # Per-call timeout (ms → s). Must be generous enough to measure slow hardware.
    # 200ms is 5× the target — gives slow machines a chance to measure accurately.
    _PER_CALL_TIMEOUT_S = 0.200

    try:
        for size in PRESIDIO_CALIBRATION_SIZES:
            text = _make_calibration_text(size)
            latencies_ms: list[float] = []

            for i in range(PRESIDIO_CALIBRATION_ITERATIONS):
                t0 = time.perf_counter()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(
                            scan_pool,
                            _presidio_calibration_scan,
                            text,
                        ),
                        timeout=_PER_CALL_TIMEOUT_S,
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    latencies_ms.append(elapsed_ms)

                except asyncio.TimeoutError:
                    # Per-call timeout exceeded → this size is too slow
                    # Record a sentinel value that will exceed the target threshold
                    elapsed_ms = _PER_CALL_TIMEOUT_S * 1000
                    latencies_ms.append(elapsed_ms)
                    logger.debug(
                        "Calibration call timed out",
                        size=size,
                        iteration=i,
                        timeout_ms=_PER_CALL_TIMEOUT_S * 1000,
                    )

            # Calculate p99 (or max for small N) from iterations
            latencies_ms.sort()
            if len(latencies_ms) >= 10:
                # True p99 index
                p99_idx = int(len(latencies_ms) * 0.99)
            else:
                # For small N (5 iterations), use max as a conservative p99 estimate.
                # This prevents optimistic outliers from setting the threshold too high.
                p99_idx = len(latencies_ms) - 1

            p99_ms = latencies_ms[p99_idx]
            measurements[size] = p99_ms

            logger.debug(
                "Calibration measurement",
                size=size,
                p99_ms=round(p99_ms, 2),
                latencies_ms=[round(lat, 2) for lat in latencies_ms],
            )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Calibration failed with exception — using conservative fallback",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return CalibrationResult.conservative_fallback(
            reason=f"Calibration exception: {type(exc).__name__}: {exc}"
        )

    # Derive thresholds from measured latencies
    result = derive_thresholds(measurements)

    logger.info(
        "Presidio calibration complete",
        tier=result.tier,
        sync_cap=result.sync_cap,
        timeout_ms=round(result.timeout_ms, 1),
        measurements={k: round(v, 2) for k, v in measurements.items()},
    )
    return result


def _presidio_calibration_scan(text: str) -> None:
    """Worker-side calibration scan. Executes in the Presidio worker process.

    Calls the pre-loaded Presidio analyzer on the given text and discards the
    result — we only care about latency. Must be a top-level function (not a
    lambda or nested function) for ProcessPoolExecutor compatibility.

    This function imports from presidio_worker only if it is available.
    If the worker module is not yet implemented (E-003 stub phase), returns
    immediately as a no-op (calibration will measure overhead = ~0ms, which
    is safe — it just means sync_cap will be 1000 with a tight timeout).
    """
    try:
        # Import lazily — only available after E-003 implements presidio_worker.py
        from app.scanner.presidio_worker import presidio_scan_worker  # type: ignore[import]
        presidio_scan_worker(text)
    except ImportError:
        # E-003 not yet implemented — no-op. Calibration will observe near-zero
        # latency and set sync_cap=1000, timeout=PRESIDIO_TIMEOUT_MIN_S.
        # The actual Presidio latency will be measured once E-003 is shipped.
        pass
