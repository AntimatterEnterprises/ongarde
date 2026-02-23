"""AuditEvent dataclass and type aliases for the OnGarde audit backend.

All audit events flow through AuditEvent. The type aliases enforce the
Literal string unions used throughout the audit pipeline.

IMPORTANT: redacted_excerpt MUST NEVER contain raw credentials, SSNs,
credit card numbers, or full PII. See AuditEvent docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

# ─── Type Aliases ─────────────────────────────────────────────────────────────

ActionType = Literal["ALLOW", "BLOCK", "ALLOW_SUPPRESSED"]
DirectionType = Literal["REQUEST", "RESPONSE"]
RiskLevelType = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]


# ─── AuditEvent ───────────────────────────────────────────────────────────────


@dataclass
class AuditEvent:
    """Complete audit record for every proxied request/response.

    schema_version=1 — increment on breaking schema changes.

    IMPORTANT — redacted_excerpt safety contract:
        redacted_excerpt must NEVER contain raw credentials, SSNs,
        credit card numbers, or any full PII. Maximum 100 characters.
        This field must contain only sanitized context (e.g., "...prefix
        [CREDENTIAL] suffix..."). Enforcement is the responsibility of the
        scan pipeline call site, not this dataclass. Integration tests in
        E-005-S-007 will assert that no raw credential patterns appear in
        any stored redacted_excerpt.

    Field reference (architecture.md §4.1):
        Required at construction: scan_id, timestamp, user_id, action, direction
        Always present: schema_version (default=1)
        Conditional (BLOCK / ALLOW_SUPPRESSED): rule_id, risk_level, redacted_excerpt
        Optional: test, tokens_delivered, truncated, original_length,
                  advisory_presidio_entities, allowlist_rule_id

    Usage at call sites (non-negotiable):
        asyncio.create_task(backend.log_event(event))  # fire-and-forget ONLY
        # NEVER: await backend.log_event(event)
    """

    # ── Required fields (no defaults) ─────────────────────────────────────────
    scan_id: str
    """ULID-format unique identifier for this scan event."""
    timestamp: datetime
    """UTC datetime of the scan event. No timezone enforcement — caller convention is UTC."""
    user_id: str
    """Authenticated user ID (from API key validation; populated by E-006)."""
    action: ActionType
    """Scan decision: 'ALLOW', 'BLOCK', or 'ALLOW_SUPPRESSED'."""
    direction: DirectionType
    """Request direction: 'REQUEST' (incoming) or 'RESPONSE' (outgoing from upstream)."""

    # ── Always present ─────────────────────────────────────────────────────────
    schema_version: int = 1
    """Schema version tag. Always 1 for v1 events. Increment on breaking changes."""

    # ── Conditional required (BLOCK / ALLOW_SUPPRESSED) ───────────────────────
    rule_id: Optional[str] = None
    """Pattern rule ID that triggered the BLOCK (e.g., 'CREDENTIAL_DETECTED')."""
    risk_level: Optional[RiskLevelType] = None
    """Risk classification: 'CRITICAL', 'HIGH', 'MEDIUM', or 'LOW'. None for ALLOW."""
    redacted_excerpt: Optional[str] = None
    """Sanitized excerpt showing match context. ≤ 100 chars. MUST NOT contain raw credentials,
    SSNs, credit card numbers, or full PII. Use [CREDENTIAL] / [REDACTED] placeholders."""

    # ── Optional fields ────────────────────────────────────────────────────────
    test: bool = False
    """True if the triggering request used a test API key (ong-test-* prefix)."""
    tokens_delivered: Optional[int] = None
    """For streaming BLOCK events: number of tokens delivered before abort."""
    truncated: bool = False
    """True if the response was truncated mid-stream during a BLOCK."""
    original_length: Optional[int] = None
    """Original body length in bytes before truncation (if truncated=True)."""
    advisory_presidio_entities: Optional[list[str]] = None
    """Presidio entity types detected in advisory (non-blocking) scan pass.
    Example: ['CREDIT_CARD', 'US_SSN']. Never includes the raw matched text."""
    allowlist_rule_id: Optional[str] = None
    """If action='ALLOW_SUPPRESSED': the allowlist rule_id that suppressed the block."""
