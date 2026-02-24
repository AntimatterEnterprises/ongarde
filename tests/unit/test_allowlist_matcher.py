"""Tests for apply_allowlist() — E-009-S-002.

Tests:
  - No entries → returns original BLOCK unchanged
  - Matching rule_id → returns ALLOW_SUPPRESSED
  - Non-matching rule_id → returns original BLOCK
  - Pattern present and matches → ALLOW_SUPPRESSED
  - Pattern present but no match → original BLOCK
  - ALLOW result passes through unchanged
  - allowlist_rule_id set on suppression
  - Original rule_id preserved in suppressed result
  - Fail-safe: exception → original BLOCK returned

Story: E-009-S-002
"""

from __future__ import annotations

from app.allowlist.loader import AllowlistEntry
from app.allowlist.matcher import apply_allowlist
from app.models.scan import Action, RiskLevel, ScanResult

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _block(rule_id: str = "CREDENTIAL_DETECTED", scan_id: str = "01TEST") -> ScanResult:
    return ScanResult(
        action=Action.BLOCK,
        rule_id=rule_id,
        risk_level=RiskLevel.CRITICAL,
        scan_id=scan_id,
        redacted_excerpt="[REDACTED]",
    )


def _allow(scan_id: str = "01TEST") -> ScanResult:
    return ScanResult(
        action=Action.ALLOW,
        scan_id=scan_id,
    )


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestApplyAllowlist:
    def test_no_entries_returns_original_block(self):
        result = _block()
        out = apply_allowlist(result, "some content", [])
        assert out is result  # Same object (no copy made)
        assert out.action == Action.BLOCK

    def test_matching_rule_id_returns_allow_suppressed(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "some content", [entry])
        assert out.action == Action.ALLOW_SUPPRESSED

    def test_non_matching_rule_id_returns_original_block(self):
        entry = AllowlistEntry(rule_id="OTHER_RULE")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "some content", [entry])
        assert out.action == Action.BLOCK

    def test_allow_result_passes_through_unchanged(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = _allow()
        out = apply_allowlist(result, "some content", [entry])
        assert out is result
        assert out.action == Action.ALLOW

    def test_pattern_match_suppresses_block(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED", pattern="test_key")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "here is my test_key value", [entry])
        assert out.action == Action.ALLOW_SUPPRESSED

    def test_pattern_no_match_keeps_block(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED", pattern="prod_secret")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "here is my test_key value", [entry])
        assert out.action == Action.BLOCK

    def test_allowlist_rule_id_set_on_suppression(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED", note="test key")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "content", [entry])
        assert out.allowlist_rule_id == "CREDENTIAL_DETECTED"

    def test_original_rule_id_preserved_in_suppression(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "content", [entry])
        assert out.rule_id == "CREDENTIAL_DETECTED"  # Not changed

    def test_original_risk_level_preserved(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "content", [entry])
        assert out.risk_level == RiskLevel.CRITICAL  # Preserved

    def test_original_scan_id_preserved(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = _block(rule_id="CREDENTIAL_DETECTED", scan_id="01MYSCANID")
        out = apply_allowlist(result, "content", [entry])
        assert out.scan_id == "01MYSCANID"

    def test_original_redacted_excerpt_preserved(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = _block()
        out = apply_allowlist(result, "content", [entry])
        assert out.redacted_excerpt == "[REDACTED]"

    def test_first_matching_entry_wins(self):
        entries = [
            AllowlistEntry(rule_id="OTHER"),
            AllowlistEntry(rule_id="CREDENTIAL_DETECTED"),
            AllowlistEntry(rule_id="CREDENTIAL_DETECTED", note="second match"),
        ]
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "content", entries)
        assert out.action == Action.ALLOW_SUPPRESSED
        assert out.allowlist_rule_id == "CREDENTIAL_DETECTED"

    def test_pattern_with_re2_search_not_fullmatch(self):
        # re2.search() — pattern can match anywhere in string
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED", pattern="sk-test")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        # "sk-test" appears in the middle — should match
        out = apply_allowlist(result, "prefix sk-test-12345 suffix", [entry])
        assert out.action == Action.ALLOW_SUPPRESSED

    def test_allow_suppressed_action_on_suppressed_result(self):
        entry = AllowlistEntry(rule_id="PROMPT_INJECTION")
        result = _block(rule_id="PROMPT_INJECTION")
        out = apply_allowlist(result, "content", [entry])
        assert out.action == Action.ALLOW_SUPPRESSED

    def test_empty_content_with_no_pattern_suppresses(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "", [entry])
        assert out.action == Action.ALLOW_SUPPRESSED

    def test_empty_content_with_pattern_blocks(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED", pattern="sk-test")
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "", [entry])
        assert out.action == Action.BLOCK

    def test_multiple_entries_first_rule_id_match_wins(self):
        entries = [
            AllowlistEntry(rule_id="CREDENTIAL_DETECTED", pattern="no_match_pattern"),
            AllowlistEntry(rule_id="CREDENTIAL_DETECTED"),  # No pattern — always matches
        ]
        result = _block(rule_id="CREDENTIAL_DETECTED")
        out = apply_allowlist(result, "content without pattern", entries)
        # First entry has pattern that doesn't match, second entry (no pattern) matches
        assert out.action == Action.ALLOW_SUPPRESSED
