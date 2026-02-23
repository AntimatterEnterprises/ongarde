"""Health utility classes and functions for OnGarde.

Provides:
  - ScanLatencyTracker — rolling window of last 100 scan latencies (avg, p99)
  - ScannerHealth      — dataclass for scanner health snapshot
  - check_scanner_health() — async stub that returns healthy/zero values
                             (real Presidio pool introspection in E-003)

Architecture references:
  - architecture.md §11.4 — ScanLatencyTracker rolling window spec
  - E-001-S-007 story     — required interface and stub behaviour

Stories:
  E-001-S-007 — initial implementation (stub, always healthy)
  E-003-S-007 — will replace stub with real pool health check
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

# ─── Data Types ───────────────────────────────────────────────────────────────


@dataclass
class ScannerHealth:
    """Snapshot of scanner subsystem health.

    Attributes:
        healthy:         True when scanner pool is operational (stub: always True).
        avg_latency_ms:  Rolling average of last 100 scan durations.
        p99_latency_ms:  p99 latency of last 100 scan durations.
        queue_depth:     Current number of pending scan tasks (stub: always 0).
    """

    healthy: bool
    avg_latency_ms: float
    p99_latency_ms: float
    queue_depth: int


# ─── ScanLatencyTracker ───────────────────────────────────────────────────────


class ScanLatencyTracker:
    """Rolling window of scan latency measurements (last *window* samples).

    Used by ``/health`` and ``/health/scanner`` to report ``avg_scan_ms`` and
    ``p99_scan_ms`` without storing unbounded history.

    Thread-safety:
        Safe for single-threaded asyncio use (all access from the event loop).
        NOT safe for concurrent OS-thread access (not needed here).

    Args:
        window: Maximum number of samples to retain (default 100, per architecture §11.4).

    Usage::

        tracker = ScanLatencyTracker()
        tracker.record(12.3)      # add a measurement
        avg  = tracker.avg_ms     # rolling average
        p99  = tracker.p99_ms     # p99 (0.0 until 10+ samples)
        n    = tracker.count      # current sample count
    """

    def __init__(self, window: int = 100) -> None:
        self._times: deque[float] = deque(maxlen=window)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def record(self, duration_ms: float) -> None:
        """Append a latency sample to the rolling window.

        When the window is full the oldest sample is automatically evicted
        (``collections.deque(maxlen=...)`` behaviour).

        Args:
            duration_ms: Scan duration in milliseconds.
        """
        self._times.append(duration_ms)

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def avg_ms(self) -> float:
        """Rolling arithmetic mean of all samples in the window.

        Returns:
            0.0 when the window is empty; otherwise the mean of all current samples.
        """
        if not self._times:
            return 0.0
        return sum(self._times) / len(self._times)

    @property
    def p99_ms(self) -> float:
        """99th percentile of samples in the rolling window.

        Per architecture §11.4: returns 0.0 when fewer than 10 samples are
        available (avoids misleading p99 values from tiny sample sets).

        Returns:
            0.0 if fewer than 10 samples; otherwise the p99 latency in ms.
        """
        if len(self._times) < 10:
            return 0.0
        sorted_times = sorted(self._times)
        # Use floor index so we never go out-of-bounds
        idx = max(0, int(len(sorted_times) * 0.99) - 1)
        return sorted_times[idx]

    @property
    def count(self) -> int:
        """Number of samples currently in the window (0 ≤ count ≤ window)."""
        return len(self._times)


# ─── Scanner Health Check ─────────────────────────────────────────────────────


async def check_scanner_health(
    scan_pool: Optional[object],
    latency_tracker: Optional[ScanLatencyTracker] = None,
) -> ScannerHealth:
    """Return a health snapshot for the scanner subsystem.

    **Stub implementation** (E-001-S-007): always reports healthy with zero
    queue depth.  The real ProcessPoolExecutor introspection is implemented in
    E-003-S-007 and will replace the ``healthy`` and ``queue_depth`` values.

    Args:
        scan_pool:       The ProcessPoolExecutor instance (or ``None`` when the
                         pool is not yet initialised, e.g. in lite mode or stub).
        latency_tracker: Optional ``ScanLatencyTracker`` to read avg/p99 from.
                         Falls back to a fresh (all-zero) tracker when ``None``.

    Returns:
        ``ScannerHealth`` with current metrics.
    """
    tracker: ScanLatencyTracker = latency_tracker or ScanLatencyTracker()

    # Stub: scan_pool is None in this story; real pool health check in E-003
    healthy: bool = True  # E-003 will interrogate pool._processes to detect crashes
    queue_depth: int = 0  # E-003 will read pool._work_queue.qsize()

    return ScannerHealth(
        healthy=healthy,
        avg_latency_ms=tracker.avg_ms,
        p99_latency_ms=tracker.p99_ms,
        queue_depth=queue_depth,
    )


# ─── StreamingMetricsTracker (E-004-S-005) ───────────────────────────────────


class StreamingMetricsTracker:
    """Tracks active streaming connections and per-window scan latencies.

    Used by ``/health/scanner`` to expose streaming-specific metrics distinct
    from the full-request scan latency tracked by ``ScanLatencyTracker``.

    Thread-safety:
        Safe for single-threaded asyncio access only. NOT safe for concurrent
        OS-thread access (no locks — asyncio single-event-loop guarantee).

    Fields surfaced by ``/health/scanner``:
        streaming_active:    Current number of open SSE streaming connections.
        window_scan_avg_ms:  Rolling average of per-window scan durations.
        window_scan_p99_ms:  p99 of per-window scan durations.
        window_scan_count:   Total window scans recorded (for observability).

    Usage::

        tracker = StreamingMetricsTracker()

        # In _stream_response_scan():
        tracker.stream_opened()
        try:
            ...
        finally:
            tracker.stream_closed()

        # In StreamingScanner on_window_scan callback:
        tracker.record_window_scan(elapsed_ms)

    Args:
        window: Maximum number of window scan samples to retain (default 100).

    Stories: E-004-S-005 (this class)
    """

    def __init__(self, window: int = 100) -> None:
        self._active: int = 0
        self._window_times: deque[float] = deque(maxlen=window)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def stream_opened(self) -> None:
        """Call when a streaming connection starts. Increments active count."""
        self._active += 1

    def stream_closed(self) -> None:
        """Call when a streaming connection ends (normal or aborted).

        Decrements active count. Never goes below 0 (defensive against
        double-close races in pathological shutdown scenarios).
        """
        self._active = max(0, self._active - 1)

    def record_window_scan(self, duration_ms: float) -> None:
        """Record a per-window scan duration in milliseconds.

        Called by the StreamingScanner ``on_window_scan`` callback.

        Args:
            duration_ms: Elapsed time for a single window scan (ms).
        """
        self._window_times.append(duration_ms)

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def active_count(self) -> int:
        """Current number of open streaming connections (gauge, ≥ 0)."""
        return self._active

    @property
    def window_avg_ms(self) -> float:
        """Rolling arithmetic mean of recorded window scan durations.

        Returns 0.0 when no measurements exist.
        """
        if not self._window_times:
            return 0.0
        return sum(self._window_times) / len(self._window_times)

    @property
    def window_p99_ms(self) -> float:
        """99th percentile of recorded window scan durations.

        Returns 0.0 when fewer than 10 measurements are available
        (avoids misleading p99 from tiny sample sets — consistent with
        ScanLatencyTracker.p99_ms behaviour).
        """
        if len(self._window_times) < 10:
            return 0.0
        sorted_times = sorted(self._window_times)
        idx = max(0, int(len(sorted_times) * 0.99) - 1)
        return sorted_times[idx]

    @property
    def window_count(self) -> int:
        """Number of window scan measurements in the rolling window."""
        return len(self._window_times)
