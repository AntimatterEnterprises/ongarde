"""Scan pipeline internals — E-002-S-006, E-003-S-005, E-003-S-006.

Provides ``scan_request()``: the internal pipeline function called ONLY from
``scan_or_block()`` in ``safe_scan.py``. Do NOT import directly from proxy code.

IMPORT RULES:
  - ``import re2`` ONLY — ``import re`` is PROHIBITED in this file.
  - CI lint gate: grep -r "^import re$|^from re import|^import re " app/scanner/

Architecture reference: architecture.md §2.1, §5.1, §5.2, §13
Stories: E-002-S-006 (pipeline + stubs), E-003-S-005 (real Presidio), E-003-S-006 (timeouts)
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

import re2  # noqa: F401 — google-re2. NEVER: import re

from app.constants import DEFAULT_PRESIDIO_SYNC_CAP, PRESIDIO_TIMEOUT_FALLBACK_S
from app.models.scan import Action, RiskLevel, ScanResult
from app.scanner.presidio_worker import presidio_scan_worker
from app.scanner.regex_engine import (
    apply_input_cap,
    make_redacted_excerpt,
    make_suppression_hint,
    regex_scan,
)

logger = logging.getLogger(__name__)

# ─── Adaptive Performance Protocol — Runtime-Calibrated Thresholds ────────────
# These values are SET by update_calibration() during lifespan startup (HOLD-002 fix).
# They start at conservative defaults and are updated BEFORE app.state.ready = True.
# No hard-coded hardware assumptions — actual values depend on measured hardware p99.
#
# Thread-safety: these are set once at startup (before any requests are served) and
# then read-only during the server's lifetime. No lock needed (GIL protects scalar writes).

#: Presidio sync threshold: inputs up to this length use synchronous Presidio scan.
#: Inputs above this threshold use advisory background scan.
#: Default = conservative fallback; overridden by calibration at startup.
PRESIDIO_SYNC_CAP: int = DEFAULT_PRESIDIO_SYNC_CAP  # chars

#: Per-operation Presidio timeout (seconds).
#: Default = maximum conservative; overridden by calibration at startup.
#: SCANNER_TIMEOUT → HTTP 400 (not 500) when this fires (E-003-S-006).
PRESIDIO_TIMEOUT_S: float = PRESIDIO_TIMEOUT_FALLBACK_S  # seconds

# Advisory scan budget: 3× the sync timeout (background — doesn't gate response)
_ADVISORY_TIMEOUT_MULTIPLIER: float = 3.0


def update_calibration(sync_cap: int, timeout_s: float) -> None:
    """Update engine thresholds from calibration result (called at startup only).

    MUST be called BEFORE app.state.ready = True — never during request handling.
    Updates the module-level PRESIDIO_SYNC_CAP and PRESIDIO_TIMEOUT_S that
    scan_request() uses for sync/advisory routing and timeout enforcement.

    Args:
        sync_cap:  Effective PRESIDIO_SYNC_CAP chars for this hardware.
        timeout_s: Effective PRESIDIO_TIMEOUT_S seconds for this hardware.
    """
    global PRESIDIO_SYNC_CAP, PRESIDIO_TIMEOUT_S
    PRESIDIO_SYNC_CAP = sync_cap
    PRESIDIO_TIMEOUT_S = timeout_s
    logger.info(
        "Engine thresholds updated from calibration",
        presidio_sync_cap=sync_cap,
        presidio_timeout_ms=round(timeout_s * 1000, 1),
    )


async def scan_request(
    text: str,
    scan_pool: Optional[ProcessPoolExecutor],
    scan_id: str,
    audit_context: dict,
) -> ScanResult:
    """Internal scan pipeline. Called ONLY from scan_or_block(). Not for direct import.

    Step 1: Apply input cap (8192 chars hard truncation)
    Step 2: Regex fast path (always, < 1ms synchronous)
    Step 3: Presidio NLP — routing by calibrated sync_cap
             - PRESIDIO_SYNC_CAP is set by update_calibration() at startup (not hard-coded).
             - See architecture.md §13 — Adaptive Performance Protocol.
             - inputs ≤ sync_cap → synchronous Presidio with PRESIDIO_TIMEOUT_S gate
             - inputs > sync_cap → advisory background task (non-blocking)
             - PRESIDIO_SYNC_CAP=0 (minimal tier) → all inputs go advisory
    Step 4: Return ALLOW (regex passed, no PII detected in sync path)

    PRECONDITION: Callers (scan_or_block) must wrap this in asyncio.wait_for()
    with SCANNER_GLOBAL_TIMEOUT_S (60ms) as the outer safety net.

    Args:
        text:          Request body decoded as UTF-8. Raw, uncapped.
        scan_pool:     Presidio ProcessPoolExecutor. None → regex-only mode.
        scan_id:       ULID for this request — propagated to ScanResult.
        audit_context: Mutable dict for tracing/audit metadata.

    Returns:
        ScanResult(action=BLOCK) if PII/credentials detected, else ScanResult(ALLOW).
    """
    # ── Step 1: Hard input cap ────────────────────────────────────────────────
    # Non-negotiable first step — must precede ALL scanning logic.
    text = apply_input_cap(text, audit_context)

    # ── Step 2: Regex fast path ───────────────────────────────────────────────
    # Synchronous, < 1ms, never raises (built-in fail-safe in regex_scan()).
    # AC-CALIB-008: Regex fast-path operates at full speed on ANY calibration tier.
    regex_result = regex_scan(text)
    if regex_result.is_block:
        redacted = make_redacted_excerpt(text, regex_result)
        hint: Optional[str]
        if regex_result.rule_id in ("SCANNER_ERROR", "SCANNER_TIMEOUT"):
            hint = None
        else:
            hint = make_suppression_hint(
                regex_result.rule_id or "UNKNOWN",
                regex_result.matched_slug or "unknown",
            )
        return ScanResult(
            action=Action.BLOCK,
            scan_id=scan_id,
            rule_id=regex_result.rule_id,
            risk_level=regex_result.risk_level,
            redacted_excerpt=redacted,
            suppression_hint=hint,
            test=regex_result.test,
        )

    # ── Step 3: Presidio NLP — routing by calibrated sync_cap ────────────────
    # PRESIDIO_SYNC_CAP is set by update_calibration() at startup (not hard-coded).
    # See architecture.md §13 — Adaptive Performance Protocol.
    if scan_pool is not None:
        if PRESIDIO_SYNC_CAP > 0 and len(text) <= PRESIDIO_SYNC_CAP:
            # Synchronous Presidio scan: short inputs within calibrated threshold
            # _presidio_sync_scan() applies asyncio.wait_for(PRESIDIO_TIMEOUT_S) internally
            return await _presidio_sync_scan(text, scan_pool, scan_id)
        elif len(text) > 0:
            # Advisory background scan: long inputs or minimal tier
            # Non-blocking — fires and forgets; result enriches audit only
            asyncio.create_task(
                _presidio_advisory_scan(text, scan_pool, scan_id, audit_context)
            )

    # ── Step 4: ALLOW ─────────────────────────────────────────────────────────
    return ScanResult(action=Action.ALLOW, scan_id=scan_id)


async def _presidio_sync_scan(
    text: str,
    scan_pool: ProcessPoolExecutor,
    scan_id: str,
) -> ScanResult:
    """Presidio NLP synchronous gate — E-003-S-005 implementation.

    Runs presidio_scan_worker() in the ProcessPoolExecutor via run_in_executor.
    Applies asyncio.wait_for(PRESIDIO_TIMEOUT_S) to enforce per-operation timeout.

    AC-S006-01: PRESIDIO_TIMEOUT_S is the module-level runtime variable (not hard-coded 40ms).
    AC-E003-06: asyncio.TimeoutError propagates out to scan_or_block() → SCANNER_TIMEOUT → HTTP 400.

    Args:
        text:      Text to scan (already capped at INPUT_HARD_CAP).
        scan_pool: Live ProcessPoolExecutor with warm presidio_worker_init() worker.
        scan_id:   ULID for this scan.

    Returns:
        ScanResult(BLOCK) if PII detected, ScanResult(ALLOW) otherwise.

    Raises:
        asyncio.TimeoutError: If Presidio scan exceeds PRESIDIO_TIMEOUT_S.
                              Propagates to scan_or_block() → SCANNER_TIMEOUT BLOCK.
        Exception: Any other exception propagates to scan_or_block() → SCANNER_ERROR BLOCK.
    """
    entities: list[dict] = await _run_presidio_in_executor(
        scan_pool, text, PRESIDIO_TIMEOUT_S  # runtime-calibrated — set by update_calibration()
    )
    if entities:
        return _make_presidio_block_result(entities, text, scan_id)
    return ScanResult(action=Action.ALLOW, scan_id=scan_id)


async def _presidio_advisory_scan(
    text: str,
    scan_pool: ProcessPoolExecutor,
    scan_id: str,
    audit_context: dict,
) -> None:
    """Advisory Presidio scan for long inputs — E-003-S-005 implementation.

    Non-blocking: fires via asyncio.create_task() — result does NOT gate response.
    Result enriches audit context with PII detection metadata.

    AC-E003-09: advisory scan must not degrade sync scan latency.

    Args:
        text:          Text to scan (already capped at INPUT_HARD_CAP).
        scan_pool:     Live ProcessPoolExecutor.
        scan_id:       ULID for this scan.
        audit_context: Mutable dict — updated with advisory_pii_detected.
    """
    advisory_timeout = PRESIDIO_TIMEOUT_S * _ADVISORY_TIMEOUT_MULTIPLIER
    try:
        entities: list[dict] = await _run_presidio_in_executor(
            scan_pool, text, advisory_timeout
        )
        if entities:
            audit_context["advisory_pii_detected"] = True
            audit_context["advisory_entities"] = [e["entity_type"] for e in entities]
            logger.info(
                "[%s] Advisory Presidio: PII detected in long input",
                scan_id,
                extra={"entities": [e["entity_type"] for e in entities]},
            )
        else:
            audit_context["advisory_pii_detected"] = False

    except asyncio.TimeoutError:
        logger.debug("[%s] Advisory Presidio scan timed out", scan_id)
        audit_context["advisory_pii_detected"] = None  # unknown — timed out

    except Exception as exc:  # noqa: BLE001
        logger.debug("[%s] Advisory Presidio scan error: %s", scan_id, exc)
        audit_context["advisory_pii_detected"] = None  # unknown — error


async def _run_presidio_in_executor(
    scan_pool: ProcessPoolExecutor,
    text: str,
    timeout_s: float,
) -> list[dict]:
    """Run presidio_scan_worker in the process pool with a timeout.

    Extracted as a helper for testability — allows tests to patch this function
    without dealing with asyncio/concurrent.futures mock complexity.

    Args:
        scan_pool: ProcessPoolExecutor with warm presidio_worker_init() worker.
        text:      Text to scan.
        timeout_s: Timeout in seconds. asyncio.TimeoutError raised on expiry.

    Returns:
        List of entity dicts from presidio_scan_worker().

    Raises:
        asyncio.TimeoutError: If scan exceeds timeout_s.
        Exception: Any exception from presidio_scan_worker() propagates up.
    """
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(scan_pool, presidio_scan_worker, text),
        timeout=timeout_s,
    )


def _make_presidio_block_result(
    entities: list[dict],
    text: str,
    scan_id: str,
) -> ScanResult:
    """Convert Presidio entity dicts to a BLOCK ScanResult.

    Selects the highest-confidence entity as the primary detection and formats
    a redacted excerpt for the BLOCK response body / audit event.

    AC-S005-03: rule_id format "PRESIDIO_{ENTITY_TYPE}", risk_level=RiskLevel.HIGH

    Args:
        entities: List of entity dicts from presidio_scan_worker() (non-empty).
        text:     Full input text (for excerpt extraction).
        scan_id:  ULID for this scan.

    Returns:
        ScanResult with action=BLOCK, rule_id="PRESIDIO_{ENTITY_TYPE}", risk_level=HIGH.
    """
    # Select highest-confidence entity as the primary detection
    primary = max(entities, key=lambda e: e["score"])
    entity_type: str = primary["entity_type"]
    rule_id = f"PRESIDIO_{entity_type}"

    # Redact the matched span with context
    start: int = primary["start"]
    end: int = primary["end"]
    context_window = 20
    excerpt_start = max(0, start - context_window)
    excerpt_end = min(len(text), end + context_window)
    raw_excerpt = text[excerpt_start:excerpt_end]

    # Replace the matched span with [REDACTED]
    relative_start = start - excerpt_start
    relative_end = end - excerpt_start
    redacted = (
        raw_excerpt[:relative_start]
        + "[REDACTED]"
        + raw_excerpt[relative_end:]
    )

    suppression_hint = make_suppression_hint(rule_id, entity_type.lower())

    return ScanResult(
        action=Action.BLOCK,
        scan_id=scan_id,
        rule_id=rule_id,
        risk_level=RiskLevel.HIGH,
        redacted_excerpt=redacted,
        suppression_hint=suppression_hint,
        test=False,
    )
