"""Regex engine — E-002-S-002.

Provides:
  - ``RegexScanResult``: frozen dataclass for regex scan results (INTERNAL ONLY).
  - ``apply_input_cap()``: hard 8192-char input limit; first step in scan pipeline.
  - ``regex_scan()``: apply all pre-compiled patterns; fail-safe; never raises.
  - ``make_redacted_excerpt()``: sanitize matched content for BLOCK response / audit (E-002-S-005).
  - ``make_suppression_hint()``: generate allowlist YAML snippet (E-002-S-005).

IMPORT RULES:
  - ``import re2`` ONLY — ``import re`` is PROHIBITED in this file.
  - CI lint gate: grep -r "^import re$|^from re import|^import re " app/scanner/

Architecture references: architecture.md §2.1, §2.5, §5.1
Stories: E-002-S-002 (core), E-002-S-005 (redaction + hints added to this file)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import re2  # google-re2 — NOT stdlib re

from app.models.scan import RiskLevel
from app.scanner.definitions import (
    CREDENTIAL_PATTERNS,
    DANGEROUS_COMMAND_PATTERNS,
    PII_FAST_PATH_PATTERNS,
    PROMPT_INJECTION_PATTERNS,
    TEST_CREDENTIAL_PATTERN,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Hard input cap for the scan pipeline. Enforced by apply_input_cap().
#: Inputs longer than this are truncated BEFORE regex_scan() is called.
INPUT_HARD_CAP: int = 8192

#: Context window (chars) on each side of the match for redacted_excerpt.
_EXCERPT_CONTEXT: int = 20


# ---------------------------------------------------------------------------
# RegexScanResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegexScanResult:
    """Result of a single regex_scan() call.

    INTERNAL TYPE — never serialized to HTTP responses or audit events directly.
    Use ``make_redacted_excerpt()`` and ``make_suppression_hint()`` to build
    the safe, serializable fields for ``ScanResult``.

    Fields:
        is_block:      True if any pattern matched and the request must be blocked.
        rule_id:       Identifier of the triggering rule (None on ALLOW).
        risk_level:    RiskLevel of the match (None on ALLOW).
        matched_slug:  PatternEntry.slug of the matching pattern (for suppression_hint).
        raw_match:     Actual matched text — INTERNAL ONLY; never included in responses.
                       Used ONLY by make_redacted_excerpt() to generate the sanitized excerpt.
        match_start:   Character offset where the match begins in the input text.
        match_end:     Character offset where the match ends in the input text.
        test:          True when the matched content is the known OnGarde test credential.
    """

    is_block: bool
    rule_id: Optional[str] = None
    risk_level: Optional[RiskLevel] = None
    matched_slug: Optional[str] = None
    # INTERNAL ONLY — never serialized to response or audit event.
    # The HTTP response body must never contain this value.
    raw_match: Optional[str] = None
    match_start: Optional[int] = None
    match_end: Optional[int] = None
    test: bool = False


# ---------------------------------------------------------------------------
# apply_input_cap() — AC-S002-03
# ---------------------------------------------------------------------------


def apply_input_cap(text: str, audit_context: dict) -> str:
    """Cap input at INPUT_HARD_CAP chars.

    INVARIANT: This is the FIRST step in the scan pipeline. It is called by
    ``scan_or_block()``/``scan_request()`` BEFORE ``regex_scan()``.

    ``regex_scan()`` NEVER calls this function — it is the caller's responsibility.

    On truncation, sets ``audit_context["truncated"] = True`` and
    ``audit_context["original_length"] = <original length>``.

    NEVER raises. NEVER returns None. O(1) length check when text fits.

    Args:
        text:          Request body text to cap.
        audit_context: Mutable dict for audit metadata — updated in-place on truncation.

    Returns:
        text unchanged if len <= INPUT_HARD_CAP; truncated slice otherwise.
    """
    if len(text) > INPUT_HARD_CAP:
        original_len = len(text)
        text = text[:INPUT_HARD_CAP]
        audit_context["truncated"] = True
        audit_context["original_length"] = original_len
        logger.warning(
            "Input truncated to %d chars (was %d)",
            INPUT_HARD_CAP,
            original_len,
        )
    return text


# ---------------------------------------------------------------------------
# regex_scan() — AC-S002-04
# ---------------------------------------------------------------------------


def regex_scan(text: str) -> RegexScanResult:
    """Apply all pre-compiled patterns to ``text``.

    Scanning order (first match wins — fail-fast on BLOCK):
      1. TEST_CREDENTIAL_PATTERN (exact match; short-circuits before broader patterns)
      2. CREDENTIAL_PATTERNS      (risk: CRITICAL)
      3. DANGEROUS_COMMAND_PATTERNS (risk: CRITICAL or HIGH per pattern)
      4. PROMPT_INJECTION_PATTERNS  (risk: HIGH or MEDIUM per pattern)
      5. PII_FAST_PATH_PATTERNS     (risk: HIGH)

    PRECONDITION: ``text`` has already been capped at INPUT_HARD_CAP chars.
      Callers are responsible for calling ``apply_input_cap()`` first.
      This function does NOT call ``apply_input_cap()`` internally.

    INVARIANTS:
      - Synchronous — no async, no I/O, no blocking calls.
      - NEVER raises. Any exception is caught, logged at ERROR, and returns BLOCK
        with ``rule_id="SCANNER_ERROR"`` and ``risk_level=RiskLevel.CRITICAL``.
      - Returns ``RegexScanResult(is_block=False)`` if no pattern matches.
      - Performance: < 1ms p99 for any input <= 8192 chars (benchmark verified).

    Args:
        text: Input text, pre-capped at INPUT_HARD_CAP.

    Returns:
        ``RegexScanResult(is_block=True, ...)`` on first match.
        ``RegexScanResult(is_block=False)`` if no pattern matches.
    """
    try:
        # ── Priority 1: Known test credential (exact match — short-circuits ──
        m = TEST_CREDENTIAL_PATTERN.pattern.search(text)
        if m:
            return RegexScanResult(
                is_block=True,
                rule_id=TEST_CREDENTIAL_PATTERN.rule_id,
                risk_level=TEST_CREDENTIAL_PATTERN.risk_level,
                matched_slug=TEST_CREDENTIAL_PATTERN.slug,
                raw_match=m.group(0),
                match_start=m.start(),
                match_end=m.end(),
                test=True,
            )

        # ── Priorities 2–5: Pattern groups in order ───────────────────────────
        for group in (
            CREDENTIAL_PATTERNS,
            DANGEROUS_COMMAND_PATTERNS,
            PROMPT_INJECTION_PATTERNS,
            PII_FAST_PATH_PATTERNS,
        ):
            for entry in group:
                m = entry.pattern.search(text)
                if m:
                    return RegexScanResult(
                        is_block=True,
                        rule_id=entry.rule_id,
                        risk_level=entry.risk_level,
                        matched_slug=entry.slug,
                        raw_match=m.group(0),
                        match_start=m.start(),
                        match_end=m.end(),
                        test=entry.test,
                    )

        # ── No match → ALLOW ─────────────────────────────────────────────────
        return RegexScanResult(is_block=False)

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Unexpected error in regex_scan(): %s: %s — BLOCKING",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return RegexScanResult(
            is_block=True,
            rule_id="SCANNER_ERROR",
            risk_level=RiskLevel.CRITICAL,
        )


# ---------------------------------------------------------------------------
# make_redacted_excerpt() — E-002-S-005 / AC-S005-01
# ---------------------------------------------------------------------------


def make_redacted_excerpt(
    text: str,
    result: RegexScanResult,
    max_len: int = 100,
) -> Optional[str]:
    """Produce a sanitised excerpt for the BLOCK response and audit event.

    CRITICAL and HIGH risk: the matched portion is replaced with ``[REDACTED:<slug>]``.
    Context of up to ``_EXCERPT_CONTEXT`` chars before and after the match is included.

    MEDIUM and LOW risk: first 10 chars of match + ``...`` shown as a partial hint.

    INVARIANT: NEVER returns the raw credential, API key, or private key
    in the returned string for CRITICAL/HIGH risk levels.

    Args:
        text:    The input text that was scanned (already capped at INPUT_HARD_CAP).
        result:  The RegexScanResult from regex_scan() — must have match_start/end set.
        max_len: Maximum length of the returned excerpt (default 100 chars).

    Returns:
        Sanitised excerpt string, or None for system-error blocks (no match info).
    """
    if result.match_start is None or result.match_end is None:
        # System-error block — no user content match, no excerpt possible
        return None

    # Context window: up to _EXCERPT_CONTEXT chars before and after the match
    ctx_start = max(0, result.match_start - _EXCERPT_CONTEXT)
    ctx_end = min(len(text), result.match_end + _EXCERPT_CONTEXT)

    before = text[ctx_start:result.match_start]
    after = text[result.match_end:ctx_end]
    slug = result.matched_slug or result.rule_id or "unknown"

    if result.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
        # CRITICAL/HIGH: NEVER include raw match — replace entirely
        redacted = f"{before}[REDACTED:{slug}]{after}"
    else:
        # MEDIUM/LOW: partial hint (first 10 chars of match + ...)
        raw = result.raw_match or ""
        partial = raw[:10] + ("..." if len(raw) > 10 else "")
        redacted = f"{before}[{partial}]{after}"

    return redacted[:max_len]


# ---------------------------------------------------------------------------
# make_suppression_hint() — E-002-S-005 / AC-S005-03
# ---------------------------------------------------------------------------


def make_suppression_hint(rule_id: str, slug: str) -> str:
    """Generate a ready-to-paste YAML allowlist snippet.

    The returned string is valid YAML parseable by ``yaml.safe_load()``.
    It references the triggering ``rule_id`` and provides a note template
    using the pattern ``slug`` for context.

    Deterministic: same inputs always produce the same output.

    Args:
        rule_id: The rule_id of the triggering pattern entry.
        slug:    The slug of the triggering pattern entry (from PatternEntry.slug).

    Returns:
        A multi-line YAML string that can be copy-pasted into .ongarde/config.yaml.
    """
    return (
        "# Add to .ongarde/config.yaml allowlist section:\n"
        "allowlist:\n"
        f"  - rule_id: {rule_id}\n"
        f"    note: \"explain why this {slug} is safe in your context\"\n"
    )
