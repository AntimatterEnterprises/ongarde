"""StreamingScanner — per-512-char window accumulator and regex scanner.

Provides the ``StreamingScanner`` class used by ``_stream_response_scan()`` in
``app/proxy/engine.py`` (E-004-S-002) to scan LLM streaming responses chunk-by-chunk.

Key design constraints (architecture.md §2.2):
  - ≤ 0.5ms p99 per 512-char window scan  (verified by benchmarks/bench_streaming.py)
  - Regex ONLY — Presidio is NEVER called sync on the streaming window path
  - 128-char overlap buffer prevents cross-boundary credential misses (AC-E004-05)
  - BLOCK on any scanner exception during window scan (fail-safe, architecture §5.1)
  - No blocking I/O — all operations are synchronous CPU-only (re2 regex, Python)
  - ``import re`` is PROHIBITED — google-re2 only (CI lint gate)

IMPORT RULE (CI-gated):
  ``import re2`` ONLY. Never ``import re``.

Architecture references: architecture.md §2.2, §5.1, §8
Stories: E-004-S-001 (this module), E-004-S-002 (_stream_response_scan integration)
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import re2  # noqa: F401 — google-re2. NEVER: import re

from app.models.scan import Action, RiskLevel, ScanResult
from app.scanner.regex_engine import make_redacted_excerpt, make_suppression_hint, regex_scan

logger = logging.getLogger(__name__)

# ─── Window Constants ──────────────────────────────────────────────────────────

#: Scan window size in characters. A window scan is triggered when this many chars
#: have accumulated in window_buffer since the last scan (or stream start).
#: Source: architecture.md §2.2. Non-negotiable — AC-E004-02 benchmarks this size.
WINDOW_SIZE: int = 512

#: Overlap buffer size in characters. The last OVERLAP_SIZE chars of the previous
#: window are prepended to the current scan text to catch credentials split across
#: window boundaries (AC-E004-05).
#:
#: Example: A 64-char API key split as 32+32 at a window boundary:
#:   - Window 1 scan text: window_1_chars       (misses second half of key)
#:   - Window 2 scan text: overlap + window_2   (overlap = last 128 chars of window_1)
#:     → The full key is present in the scan text → credential detected ✓
OVERLAP_SIZE: int = 128


# ─── StreamingScanner ─────────────────────────────────────────────────────────


class StreamingScanner:
    """Per-stream state machine for window-based regex scanning of SSE content.

    Instantiated once per streaming response. Each call to ``add_content()``
    accumulates extracted text and triggers a window scan every 512 chars.
    On BLOCK: sets ``aborted=True``; further calls return BLOCK immediately.

    Design properties:
      - Stateful: all fields are per-stream state (not shared between requests)
      - No async: ``_do_window_scan()`` is synchronous (re2 is non-blocking)
      - Fail-safe: any exception during window scan → returns BLOCK (architecture §5.1)
      - No Presidio: Presidio is strictly advisory (background task in E-004-S-004)

    Thread-safety:
      Single-threaded asyncio access only. NOT safe for concurrent OS-thread access.

    Args:
        scan_id:       ULID for the parent request — propagated to ScanResult.
        on_window_scan: Optional callback invoked with elapsed_ms after each window scan.
                        Used by E-004-S-005 to record latencies in StreamingMetricsTracker.

    Usage::

        scanner = StreamingScanner(scan_id="01HXXXXXXXXXXXXXXXXXXXXXXXXX")
        result = scanner.add_content("some streamed text chunk...")
        if result and result.action == Action.BLOCK:
            # abort stream
        result = scanner.flush()  # after stream ends
    """

    def __init__(
        self,
        scan_id: str,
        on_window_scan: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.scan_id: str = scan_id

        #: Accumulated content since last window scan. Reset after each window.
        self.window_buffer: str = ""

        #: Last OVERLAP_SIZE chars of the previous window. Prepended before each scan.
        self.overlap_buffer: str = ""

        #: Full accumulated content for background Presidio advisory scan (E-004-S-004).
        #: Append-only; never reset.
        self.presidio_accumulation_buffer: str = ""

        #: Byte-based approximation of tokens forwarded to the agent (len // 4).
        #: Increments with each add_content() call. Used in ongarde_block payload.
        self.tokens_delivered: int = 0

        #: Number of complete windows scanned (reset window_buffer count).
        self.window_count: int = 0

        #: True after a BLOCK decision. Subsequent add_content() calls return BLOCK.
        self.aborted: bool = False

        #: Per-window scan latencies in milliseconds (for benchmark/health tracking).
        self._window_scan_latencies: list[float] = []

        #: Optional callback: called with elapsed_ms after each window scan.
        self._on_window_scan: Optional[Callable[[float], None]] = on_window_scan

        #: ScanResult saved on abort — returned by idempotent add_content() calls.
        self._abort_result: Optional[ScanResult] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_content(self, content: str) -> Optional[ScanResult]:
        """Accumulate content and trigger a window scan when window is full.

        Args:
            content: Extracted text content from an SSE chunk (NOT raw SSE bytes).

        Returns:
            ScanResult(action=BLOCK) if a threat was detected or scanner failed.
            None if the window is not yet full (continue accumulating).

        Invariant: Once aborted=True, always returns the saved abort ScanResult.
        """
        if self.aborted:
            return self._abort_result

        # Accumulate content
        self.window_buffer += content
        self.presidio_accumulation_buffer += content

        # Byte-based token approximation (architecture.md §2.2, AC-E004-04)
        self.tokens_delivered += len(content) // 4

        # Trigger window scan when buffer reaches window size
        if len(self.window_buffer) >= WINDOW_SIZE:
            return self._do_window_scan()

        return None  # Window not yet full — continue accumulating

    def flush(self) -> Optional[ScanResult]:
        """Scan any remaining content in window_buffer after stream ends.

        Must be called when the upstream SSE stream has ended (normal completion
        or upstream [DONE] signal) to ensure the final partial window is scanned.

        Returns:
            ScanResult(action=BLOCK) if a threat was detected in the final window.
            None if the remaining buffer is empty, already aborted, or passes scan.
        """
        if self.aborted:
            return self._abort_result

        if len(self.window_buffer) > 0:
            return self._do_window_scan()

        return None  # Nothing to flush

    # ── Private Scan Logic ────────────────────────────────────────────────────

    def _do_window_scan(self) -> Optional[ScanResult]:
        """Perform a single window scan on (overlap_buffer + window_buffer).

        Returns:
            ScanResult(action=BLOCK) if BLOCK detected or exception occurred.
            None if scan PASSed (window forwarded, buffers rotated).
        """
        # Build scan text: prepend overlap buffer for cross-boundary protection
        scan_text = self.overlap_buffer + self.window_buffer

        t0 = time.perf_counter()
        try:
            regex_result = regex_scan(scan_text)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            # Record latency for observability (benchmark + health endpoint)
            self._window_scan_latencies.append(elapsed_ms)
            if self._on_window_scan is not None:
                try:
                    self._on_window_scan(elapsed_ms)
                except Exception:  # noqa: BLE001
                    pass  # Best-effort — never fail a scan over metric recording

            if regex_result is None:
                raise ValueError("regex_scan() returned None — treating as BLOCK")

            if regex_result.is_block:
                # Build suppression hint for policy blocks (not SCANNER_ERROR)
                if regex_result.rule_id in ("SCANNER_ERROR", "SCANNER_TIMEOUT"):
                    hint: Optional[str] = None
                else:
                    hint = make_suppression_hint(
                        regex_result.rule_id or "UNKNOWN",
                        regex_result.matched_slug or "unknown",
                    )

                block_result = ScanResult(
                    action=Action.BLOCK,
                    scan_id=self.scan_id,
                    rule_id=regex_result.rule_id,
                    risk_level=regex_result.risk_level,
                    redacted_excerpt=make_redacted_excerpt(scan_text, regex_result),
                    suppression_hint=hint,
                    test=getattr(regex_result, "test", False),
                )
                self.aborted = True
                self._abort_result = block_result
                logger.info(
                    "[%s] Streaming window BLOCK: rule=%s risk=%s window=%d",
                    self.scan_id,
                    regex_result.rule_id,
                    regex_result.risk_level,
                    self.window_count,
                )
                return block_result

        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._window_scan_latencies.append(elapsed_ms)
            if self._on_window_scan is not None:
                try:
                    self._on_window_scan(elapsed_ms)
                except Exception:  # noqa: BLE001
                    pass

            logger.error(
                "[%s] Window scan exception — BLOCKING (fail-safe): %s",
                self.scan_id,
                exc,
                exc_info=True,
            )
            error_result = ScanResult(
                action=Action.BLOCK,
                scan_id=self.scan_id,
                rule_id="SCANNER_ERROR",
                risk_level=RiskLevel.CRITICAL,
            )
            self.aborted = True
            self._abort_result = error_result
            return error_result

        # PASS: rotate buffers
        self.overlap_buffer = self.window_buffer[-OVERLAP_SIZE:]
        self.window_buffer = ""
        self.window_count += 1
        return None  # Pass — continue streaming
