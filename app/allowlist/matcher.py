"""Allowlist matching for OnGarde scan results — E-009-S-002.

apply_allowlist() is the ONLY function to call from scan_or_block().
It checks a BLOCK ScanResult against allowlist entries.
First matching entry wins — returns a new ALLOW_SUPPRESSED ScanResult.

IMPORT RULES:
  - `import re2` ONLY — `import re` is PROHIBITED.
  - CI lint gate: grep -r "^import re$|^from re import|^import re " app/

Architecture reference: architecture.md §E-009, epics.md AC-E009-01 through AC-E009-03
Stories: E-009-S-002
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import re2  # noqa: F401 — google-re2. NEVER: import re

from app.models.scan import Action, ScanResult
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.allowlist.loader import AllowlistEntry

logger = get_logger(__name__)


def apply_allowlist(
    scan_result: ScanResult,
    content: str,
    entries: list["AllowlistEntry"],
) -> ScanResult:
    """Check a BLOCK ScanResult against the loaded allowlist entries.

    INVARIANT:
      - NEVER raises. On any exception → return original scan_result (fail-safe).
      - Only applies to Action.BLOCK results (ALLOW passes through unchanged).
      - First matching entry wins.

    Matching criteria (AC-E009-01, AC-E009-03):
      1. entry.rule_id must equal scan_result.rule_id
      2. If entry.pattern is set, content must match the pattern (re2.search)
      3. If both conditions hold → suppress (return ALLOW_SUPPRESSED)

    Returns:
        New ALLOW_SUPPRESSED ScanResult if a match is found.
        Original scan_result unchanged if no match, or if action != BLOCK.

    Args:
        scan_result: The scan result to check.
        content:     The original request content (for pattern matching).
        entries:     List of AllowlistEntry rules to check against.
    """
    # Only apply to BLOCK results — ALLOW passes through unchanged (AC-S002-07)
    if scan_result.action != Action.BLOCK:
        return scan_result

    # Empty allowlist — fast path
    if not entries:
        return scan_result

    try:
        for entry in entries:
            # ── Step 1: rule_id must match exactly (AC-S002-02) ────────────
            if entry.rule_id != scan_result.rule_id:
                continue

            # ── Step 2: optional pattern match (AC-E009-03) ────────────────
            if entry.pattern is not None:
                try:
                    if not re2.search(entry.pattern, content):
                        # Pattern present but content doesn't match — keep blocking
                        continue
                except re2.error as exc:
                    # Invalid pattern at match time (shouldn't happen — validated at load)
                    # Treat as no-match — conservative: keep blocking
                    logger.error(
                        "Allowlist pattern match error (treating as no-match, keeping BLOCK)",
                        rule_id=entry.rule_id,
                        pattern=entry.pattern,
                        error=str(exc),
                    )
                    continue

            # ── Match found — suppress the block ──────────────────────────
            logger.info(
                "Allowlist suppressed block",
                rule_id=scan_result.rule_id,
                allowlist_rule_id=entry.rule_id,
                note=entry.note,
                has_pattern=entry.pattern is not None,
            )

            # Return new ScanResult with action=ALLOW_SUPPRESSED (AC-S002-01, AC-S002-05)
            # Preserve all other fields (rule_id, risk_level, redacted_excerpt, scan_id)
            return dataclasses.replace(
                scan_result,
                action=Action.ALLOW_SUPPRESSED,
                allowlist_rule_id=entry.rule_id,
            )

    except Exception as exc:  # noqa: BLE001
        # Fail-safe: on any unhandled error, prefer BLOCK over ALLOW (AC-S002-06)
        logger.error(
            "apply_allowlist error — returning original BLOCK (fail-safe)",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # No match found — return original BLOCK unchanged (AC-S002-04)
    return scan_result
