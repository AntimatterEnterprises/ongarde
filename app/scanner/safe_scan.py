"""Scan gate — E-002-S-006 (replaces E-001-S-006 stub).

Provides ``scan_or_block()``: the ONLY entry point for all scan operations in the
proxy handler. This is the real implementation; the E-001-S-006 stub (always ALLOW)
has been replaced.

ATOMIC WRAPPER INVARIANTS:
  - ``scan_or_block()`` ALWAYS returns a ``ScanResult`` — it NEVER raises.
  - ``scan_or_block()`` NEVER returns None.
  - On ANY failure (exception, timeout, None/invalid return from scan_request):
    returns ``Action.BLOCK`` — fail-safe policy (architecture.md §5.1).

IMPORT RULES:
  - ``import re2`` ONLY — ``import re`` is PROHIBITED in this file.
  - CI lint gate: grep -r "^import re$|^from re import|^import re " app/scanner/

Architecture reference: architecture.md §5.1, §5.2
Stories: E-001-S-006 (stub), E-002-S-006 (this — real implementation)
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Optional

import re2  # noqa: F401 — google-re2. NEVER: import re

from app.models.scan import Action, RiskLevel, ScanResult
from app.scanner.engine import scan_request

if TYPE_CHECKING:
    from app.allowlist.loader import AllowlistLoader
    from app.utils.health import ScanLatencyTracker

logger = logging.getLogger(__name__)

#: Global safety-net timeout for the entire scan pipeline (Step 1 + 2 + 3).
#: MUST be ≥ per-operation Presidio timeout (PRESIDIO_TIMEOUT_S, capped at 60ms).
#:
#: HTTP status distinction (AC-S006-03):
#:   SCANNER_TIMEOUT: Presidio exceeded calibrated timeout → HTTP 400 (policy, not server error)
#:   SCANNER_ERROR:   Unexpected scanner failure → HTTP 400 (fail-safe; client should retry)
#:   HTTP 500:        Unhandled exception reaching FastAPI → internal error (not from here)
SCANNER_GLOBAL_TIMEOUT_S: float = 0.060  # 60ms


async def scan_or_block(
    content: str,
    scan_pool: Optional[ProcessPoolExecutor],
    scan_id: str,
    audit_context: dict,
    latency_tracker: Optional["ScanLatencyTracker"] = None,
    allowlist_loader: Optional["AllowlistLoader"] = None,
) -> ScanResult:
    """ATOMIC SCAN WRAPPER — the only entry point for all scan operations.

    INVARIANT: ALWAYS returns ScanResult. NEVER raises. NEVER returns None.
    On ANY failure: returns Action.BLOCK (fail-safe).

    Failure modes handled (AC-E002-10):
      (a) scan_request() raises any exception → BLOCK (rule_id=SCANNER_ERROR)
      (b) scan_request() returns None or wrong type → BLOCK (rule_id=SCANNER_ERROR)
      (c) asyncio.TimeoutError → BLOCK (rule_id=SCANNER_TIMEOUT)

    Pipeline (when no failure):
      1. apply_input_cap() in scan_request() — 8192-char hard limit
      2. regex_scan() — regex fast path, always runs, < 1ms
      3. Presidio NLP (E-003) — only if scan_pool is not None
      4. Return ALLOW or BLOCK ScanResult

    Args:
        content:         Request body decoded as UTF-8 (errors replaced).
        scan_pool:       Presidio ProcessPoolExecutor (None → regex-only mode).
        scan_id:         ULID for this request — propagated to ScanResult.
        audit_context:   Mutable dict with tracing metadata.
        latency_tracker: Optional ScanLatencyTracker to record scan duration.
                         Passed from proxy handler via app.state.latency_tracker.

    Returns:
        ScanResult with action=ALLOW or BLOCK. Never None. Never raises.
    """
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            scan_request(
                text=content,
                scan_pool=scan_pool,
                scan_id=scan_id,
                audit_context=audit_context,
            ),
            timeout=SCANNER_GLOBAL_TIMEOUT_S,
        )

        # ── Type-safety guard (AC-S006-04) ─────────────────────────────────
        # Explicit None and wrong-type protection — belt-and-suspenders.
        if not isinstance(result, ScanResult):
            logger.critical(
                "[%s] Scanner returned invalid type: %s — BLOCKING",
                scan_id,
                type(result),
            )
            return ScanResult(
                action=Action.BLOCK,
                rule_id="SCANNER_ERROR",
                risk_level=RiskLevel.CRITICAL,
                scan_id=scan_id,
            )

        _record_latency(latency_tracker, t0)

        # ── E-009: Allowlist check (after successful scan, before BLOCK return) ──
        # Checked ONLY for BLOCK results. Never for ALLOW. Never for error paths.
        # apply_allowlist() is fail-safe (never raises — returns original on error).
        # AC-E009-01: matching allowlist entry suppresses BLOCK → ALLOW_SUPPRESSED.
        if result.action == Action.BLOCK and allowlist_loader is not None:
            entries = allowlist_loader.get_entries()
            if entries:
                from app.allowlist.matcher import apply_allowlist
                result = apply_allowlist(result, content, entries)

        return result

    except asyncio.TimeoutError:
        # Global 60ms timeout exceeded — must not let slow scanners open a bypass window
        logger.error(
            "[%s] Global scanner timeout (%.0fms) — BLOCKING",
            scan_id,
            SCANNER_GLOBAL_TIMEOUT_S * 1000,
        )
        _record_latency(latency_tracker, t0)
        return ScanResult(
            action=Action.BLOCK,
            rule_id="SCANNER_TIMEOUT",
            risk_level=RiskLevel.CRITICAL,
            scan_id=scan_id,
        )

    except Exception as exc:  # noqa: BLE001
        # Unhandled exception in scan pipeline — must BLOCK, never let through
        logger.critical(
            "[%s] Unhandled scanner exception: %s: %s — BLOCKING",
            scan_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        _record_latency(latency_tracker, t0)
        return ScanResult(
            action=Action.BLOCK,
            rule_id="SCANNER_ERROR",
            risk_level=RiskLevel.CRITICAL,
            scan_id=scan_id,
        )


def _record_latency(
    tracker: Optional["ScanLatencyTracker"],
    t0: float,
) -> None:
    """Record elapsed scan time to the latency tracker (if available).

    Args:
        tracker: ScanLatencyTracker instance or None.
        t0:      time.perf_counter() value at scan start.
    """
    if tracker is None:
        return
    elapsed_ms = (time.perf_counter() - t0) * 1000
    try:
        tracker.record(elapsed_ms)
    except Exception:  # noqa: BLE001
        pass  # Latency recording is best-effort — never fail a scan over it
