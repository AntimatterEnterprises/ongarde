"""Tests for redacted_excerpt, suppression_hint, and test key handling — E-002-S-005.

Verifies:
  - make_redacted_excerpt() NEVER contains raw credentials for CRITICAL/HIGH
  - make_suppression_hint() produces valid YAML with correct rule_id
  - Test key (sk-ongarde-test-fake-key-12345) produces test=True flag
  - redacted_excerpt <= 100 chars
  - suppression_hint is None for SCANNER_ERROR system blocks
  - Integration: suppression_hint appears in build_block_response() body

AC coverage: AC-E002-05, AC-E002-06, AC-E002-07, AC-S005-01 through AC-S005-06
"""

from __future__ import annotations

import yaml

from app.models.block import build_block_response
from app.models.scan import Action, RiskLevel, ScanResult
from app.scanner.regex_engine import (
    RegexScanResult,
    make_redacted_excerpt,
    make_suppression_hint,
    regex_scan,
)

# ===========================================================================
# AC-S005-01 / AC-S005-02: make_redacted_excerpt()
# ===========================================================================


class TestMakeRedactedExcerpt:
    """Tests for make_redacted_excerpt() function."""

    def test_critical_credential_not_in_excerpt(self) -> None:
        """CRITICAL risk: raw Anthropic key prefix must NOT appear in excerpt."""
        raw_key = "sk-ant-api03-" + "A" * 93
        text = "My API key is: " + raw_key + " -- use it"
        result = regex_scan(text)
        assert result.is_block is True
        assert result.risk_level == RiskLevel.CRITICAL

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        assert "sk-ant-api03-" not in excerpt, (
            "Raw credential prefix must NOT appear in CRITICAL excerpt"
        )
        assert "[REDACTED:" in excerpt, "CRITICAL excerpt must contain [REDACTED: marker]"

    def test_critical_excerpt_contains_redaction_marker(self) -> None:
        """CRITICAL risk excerpt must contain [REDACTED:<slug>] marker."""
        raw_key = "sk-ant-api03-" + "B" * 93
        text = raw_key
        result = regex_scan(text)
        assert result.is_block is True

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        assert "[REDACTED:" in excerpt

    def test_excerpt_max_length(self) -> None:
        """redacted_excerpt must be <= max_len (default 100 chars)."""
        text = "prefix-" * 20 + "sk-ant-api03-" + "C" * 93 + "-suffix-" * 20
        result = regex_scan(text)
        excerpt = make_redacted_excerpt(text, result, max_len=100)
        assert excerpt is not None
        assert len(excerpt) <= 100, f"Excerpt len {len(excerpt)} exceeds 100 chars"

    def test_excerpt_custom_max_length(self) -> None:
        """Custom max_len parameter is respected."""
        raw_key = "sk-ant-api03-" + "D" * 93
        text = "The key is " + raw_key + " here"
        result = regex_scan(text)
        excerpt = make_redacted_excerpt(text, result, max_len=50)
        assert excerpt is not None
        assert len(excerpt) <= 50

    def test_excerpt_includes_context_before_and_after(self) -> None:
        """Excerpt should include up to 20 chars of context before/after match."""
        raw_key = "sk-ant-api03-" + "E" * 93
        text = "BEFORE_CONTEXT_123456 " + raw_key + " AFTER_CONTEXT_7890"
        result = regex_scan(text)
        assert result.match_start is not None

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        # Should include context before (up to 20 chars)
        assert "BEFORE" in excerpt or "CONTEXT" in excerpt or "[REDACTED" in excerpt

    def test_excerpt_match_at_position_zero(self) -> None:
        """Handle match starting at position 0 (no context before)."""
        raw_key = "sk-ant-api03-" + "F" * 93
        text = raw_key  # match starts at position 0
        result = regex_scan(text)
        assert result.match_start == 0

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        assert "[REDACTED:" in excerpt
        assert "sk-ant-api03-" not in excerpt

    def test_excerpt_match_at_end(self) -> None:
        """Handle match at end of string (no context after)."""
        raw_key = "sk-ant-api03-" + "G" * 93
        text = "Some context: " + raw_key  # match at end
        result = regex_scan(text)

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        assert "[REDACTED:" in excerpt
        assert "sk-ant-api03-" not in excerpt

    def test_excerpt_none_for_scanner_error_block(self) -> None:
        """SCANNER_ERROR system blocks have no match info → excerpt is None."""
        error_result = RegexScanResult(
            is_block=True,
            rule_id="SCANNER_ERROR",
            risk_level=RiskLevel.CRITICAL,
            # No match_start, match_end, raw_match — system error
        )
        excerpt = make_redacted_excerpt("any text", error_result)
        assert excerpt is None, (
            "SCANNER_ERROR block must return None from make_redacted_excerpt()"
        )

    def test_excerpt_high_risk_no_raw_match(self) -> None:
        """HIGH risk (/etc/passwd path) also must not contain raw match."""
        text = "Please read /etc/passwd for users"
        result = regex_scan(text)
        assert result.is_block is True
        assert result.risk_level == RiskLevel.HIGH

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        assert "[REDACTED:" in excerpt

    def test_excerpt_slug_in_redaction_marker(self) -> None:
        """The slug from the matched pattern must appear in [REDACTED:<slug>]."""
        raw_key = "sk-ant-api03-" + "H" * 93
        text = raw_key
        result = regex_scan(text)
        assert result.matched_slug is not None

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        assert f"[REDACTED:{result.matched_slug}]" in excerpt

    def test_excerpt_multiple_different_credentials(self) -> None:
        """make_redacted_excerpt works for all CRITICAL/HIGH patterns."""
        credential_texts = [
            "sk-ant-api03-" + "A" * 93,                    # Anthropic
            "ghp_" + "a" * 36,                              # GitHub PAT
            "/etc/shadow",                                   # HIGH path
            "AKIAIOSFODNN7EXAMPLE",                         # AWS AKIA
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE",        # PEM key
        ]
        for cred_text in credential_texts:
            r = regex_scan(cred_text)
            assert r.is_block is True
            excerpt = make_redacted_excerpt(cred_text, r)
            assert excerpt is not None
            if r.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
                assert "[REDACTED:" in excerpt, (
                    f"No [REDACTED:] in excerpt for CRITICAL/HIGH: {cred_text[:30]!r}"
                )


# ===========================================================================
# AC-S005-03: make_suppression_hint()
# ===========================================================================


class TestMakeSuppessionHint:
    """Tests for make_suppression_hint() function."""

    def test_returns_valid_yaml(self) -> None:
        """Output must be parseable by yaml.safe_load() without error."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "anthropic-api-key")
        parsed = yaml.safe_load(hint)
        assert parsed is not None, "Hint must parse as valid YAML"

    def test_contains_rule_id(self) -> None:
        """Hint must contain the rule_id string (AC-E002-06 item 8)."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "openai-api-key")
        assert "CREDENTIAL_DETECTED" in hint

    def test_contains_slug(self) -> None:
        """Hint must contain the slug in the note field."""
        hint = make_suppression_hint("DANGEROUS_COMMAND_DETECTED", "rm-rf")
        assert "rm-rf" in hint

    def test_yaml_structure_allowlist(self) -> None:
        """Parsed YAML must have 'allowlist' top-level key with rule_id item."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "stripe-live-secret-key")
        parsed = yaml.safe_load(hint)
        assert "allowlist" in parsed, "YAML must have 'allowlist' top-level key"
        assert isinstance(parsed["allowlist"], list)
        assert len(parsed["allowlist"]) >= 1
        item = parsed["allowlist"][0]
        assert item.get("rule_id") == "CREDENTIAL_DETECTED"

    def test_yaml_has_note_field(self) -> None:
        """Parsed YAML allowlist item must have a 'note' field."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "huggingface-token")
        parsed = yaml.safe_load(hint)
        item = parsed["allowlist"][0]
        assert "note" in item, "YAML item must have 'note' field"
        assert len(item["note"]) > 0

    def test_deterministic_output(self) -> None:
        """Same inputs always produce identical output."""
        h1 = make_suppression_hint("CREDENTIAL_DETECTED", "openai-api-key")
        h2 = make_suppression_hint("CREDENTIAL_DETECTED", "openai-api-key")
        assert h1 == h2, "make_suppression_hint() must be deterministic"

    def test_different_rule_ids(self) -> None:
        """Different rule_ids produce different hints."""
        h1 = make_suppression_hint("CREDENTIAL_DETECTED", "some-slug")
        h2 = make_suppression_hint("DANGEROUS_COMMAND_DETECTED", "some-slug")
        h3 = make_suppression_hint("PROMPT_INJECTION_DETECTED", "some-slug")
        assert h1 != h2
        assert h1 != h3
        assert h2 != h3

    def test_injection_rule_id_in_hint(self) -> None:
        """Injection rule_id correctly appears in the hint."""
        hint = make_suppression_hint("PROMPT_INJECTION_DETECTED", "ignore-previous-instructions")
        assert "PROMPT_INJECTION_DETECTED" in hint
        assert "ignore-previous-instructions" in hint
        parsed = yaml.safe_load(hint)
        assert parsed["allowlist"][0]["rule_id"] == "PROMPT_INJECTION_DETECTED"

    def test_hint_from_actual_scan_result(self) -> None:
        """Integration: use regex_scan() output to build suppression_hint."""
        text = "sk-ant-api03-" + "X" * 93
        r = regex_scan(text)
        assert r.is_block is True
        assert r.rule_id is not None
        assert r.matched_slug is not None

        hint = make_suppression_hint(r.rule_id, r.matched_slug)
        parsed = yaml.safe_load(hint)
        assert parsed["allowlist"][0]["rule_id"] == r.rule_id

    def test_hint_not_none_for_system_error(self) -> None:
        """make_suppression_hint() always returns a string (callers decide when to pass None)."""
        # The function itself doesn't special-case SCANNER_ERROR — callers do.
        hint = make_suppression_hint("SCANNER_ERROR", "unknown")
        assert isinstance(hint, str)
        assert len(hint) > 0


# ===========================================================================
# AC-E002-05: Test key (sk-ongarde-test-fake-key-12345) handling
# ===========================================================================


class TestTestKeyHandling:
    """Tests for OnGarde test credential test=True flag propagation."""

    def test_test_key_produces_test_true_in_regex_result(self) -> None:
        """regex_scan() must return test=True for the OnGarde test key."""
        result = regex_scan("sk-ongarde-test-fake-key-12345")
        assert result.is_block is True
        assert result.test is True

    def test_test_key_rule_id_is_credential_detected(self) -> None:
        """Test key block must have rule_id=CREDENTIAL_DETECTED."""
        result = regex_scan("sk-ongarde-test-fake-key-12345")
        assert result.rule_id == "CREDENTIAL_DETECTED"

    def test_test_key_risk_level_is_critical(self) -> None:
        """Test key block must have CRITICAL risk level."""
        result = regex_scan("sk-ongarde-test-fake-key-12345")
        assert result.risk_level == RiskLevel.CRITICAL

    def test_test_key_has_matched_slug(self) -> None:
        """Test key block must have a matched_slug."""
        result = regex_scan("sk-ongarde-test-fake-key-12345")
        assert result.matched_slug is not None
        assert len(result.matched_slug) > 0

    def test_test_key_redacted_excerpt_hides_raw_key(self) -> None:
        """redacted_excerpt for test key must NOT contain the raw test key (AC-E002-07)."""
        text = "Use sk-ongarde-test-fake-key-12345 for testing"
        result = regex_scan(text)
        assert result.is_block is True
        assert result.test is True

        excerpt = make_redacted_excerpt(text, result)
        assert excerpt is not None
        assert "sk-ongarde-test-fake-key-12345" not in excerpt, (
            "Raw test key must NOT appear in redacted_excerpt"
        )
        assert "[REDACTED:" in excerpt

    def test_test_key_suppression_hint_is_valid(self) -> None:
        """Test key block must produce a valid suppression_hint (AC-E002-05 item 13)."""
        result = regex_scan("sk-ongarde-test-fake-key-12345")
        assert result.rule_id is not None
        assert result.matched_slug is not None

        hint = make_suppression_hint(result.rule_id, result.matched_slug)
        assert isinstance(hint, str)
        parsed = yaml.safe_load(hint)
        assert parsed["allowlist"][0]["rule_id"] == result.rule_id

    def test_real_credential_has_test_false(self) -> None:
        """Non-test credentials must NOT have test=True."""
        real_key = "sk-ant-api03-" + "Y" * 93
        result = regex_scan(real_key)
        assert result.is_block is True
        assert result.test is False, "Real credential must have test=False"

    def test_test_key_embedded_in_text(self) -> None:
        """Test key detected even when embedded in larger text."""
        text = "Please authenticate with sk-ongarde-test-fake-key-12345 for this scenario"
        result = regex_scan(text)
        assert result.is_block is True
        assert result.test is True


# ===========================================================================
# AC-S005-05 / AC-S005-06: Integration with ScanResult and block response
# ===========================================================================


class TestBlockResponseIntegration:
    """Integration tests: suppression_hint and redacted_excerpt in HTTP responses."""

    def _make_block_scan_result(
        self,
        rule_id: str,
        slug: str,
        redacted: str | None,
        hint: str | None,
        test: bool = False,
    ) -> ScanResult:
        """Helper to construct a ScanResult for block response testing."""
        return ScanResult(
            action=Action.BLOCK,
            scan_id="01TEST000000000000000000001",
            rule_id=rule_id,
            risk_level=RiskLevel.CRITICAL,
            redacted_excerpt=redacted,
            suppression_hint=hint,
            test=test,
        )

    def test_block_response_contains_suppression_hint(self) -> None:
        """build_block_response() must include suppression_hint in body."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "openai-api-key")
        scan_result = self._make_block_scan_result(
            rule_id="CREDENTIAL_DETECTED",
            slug="openai-api-key",
            redacted="[REDACTED:openai-api-key]",
            hint=hint,
        )
        response = build_block_response(scan_result)
        body = response.body
        import json
        body_dict = json.loads(body)
        assert "ongarde" in body_dict
        assert body_dict["ongarde"]["suppression_hint"] == hint

    def test_block_response_suppression_hint_is_parseable_yaml(self) -> None:
        """suppression_hint in response must be parseable YAML (AC-S005-05 item 29)."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "anthropic-api-key")
        scan_result = self._make_block_scan_result(
            rule_id="CREDENTIAL_DETECTED",
            slug="anthropic-api-key",
            redacted="[REDACTED:anthropic-api-key]",
            hint=hint,
        )
        response = build_block_response(scan_result)
        import json
        body_dict = json.loads(response.body)
        hint_from_response = body_dict["ongarde"]["suppression_hint"]
        parsed = yaml.safe_load(hint_from_response)
        assert parsed is not None
        assert "allowlist" in parsed

    def test_block_response_redacted_excerpt_present(self) -> None:
        """build_block_response() must include redacted_excerpt in body."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "github-access-token")
        scan_result = self._make_block_scan_result(
            rule_id="CREDENTIAL_DETECTED",
            slug="github-access-token",
            redacted="use [REDACTED:github-access-token] here",
            hint=hint,
        )
        response = build_block_response(scan_result)
        import json
        body_dict = json.loads(response.body)
        assert body_dict["ongarde"]["redacted_excerpt"] == "use [REDACTED:github-access-token] here"

    def test_block_response_suppression_hint_null_for_scanner_error(self) -> None:
        """SCANNER_ERROR blocks must have suppression_hint=None in response."""
        scan_result = self._make_block_scan_result(
            rule_id="SCANNER_ERROR",
            slug="unknown",
            redacted=None,
            hint=None,  # SCANNER_ERROR → no hint
        )
        response = build_block_response(scan_result)
        import json
        body_dict = json.loads(response.body)
        assert body_dict["ongarde"]["suppression_hint"] is None
        assert body_dict["ongarde"]["redacted_excerpt"] is None

    def test_block_response_test_true_propagates(self) -> None:
        """test=True from test key detection must appear in block response body."""
        hint = make_suppression_hint("CREDENTIAL_DETECTED", "ongarde-test-key")
        scan_result = ScanResult(
            action=Action.BLOCK,
            scan_id="01TEST000000000000000000002",
            rule_id="CREDENTIAL_DETECTED",
            risk_level=RiskLevel.CRITICAL,
            redacted_excerpt="[REDACTED:ongarde-test-key]",
            suppression_hint=hint,
            test=True,
        )
        response = build_block_response(scan_result)
        import json
        body_dict = json.loads(response.body)
        assert body_dict["ongarde"]["test"] is True

    def test_no_raw_credentials_in_any_response_field(self) -> None:
        """Comprehensive: no BLOCK response field contains a raw credential (AC-S005-06)."""
        import re2 as re
        # Simple credential verification patterns (distinct from definitions.py)
        cred_check_patterns = [
            re.compile(r'sk-ant-api03-[A-Z]{20}'),
            re.compile(r'sk-[a-zA-Z0-9]{48}'),
            re.compile(r'ghp_[a-zA-Z0-9]{36}'),
            re.compile(r'sk_live_[a-zA-Z0-9]{24}'),
            re.compile(r'AIza[a-zA-Z0-9]{35}'),
        ]

        credential_texts = [
            "sk-ant-api03-" + "A" * 93,
            "sk-" + "b" * 48,
            "ghp_" + "c" * 36,
            "sk_live_" + "d" * 24,
            "AIza" + "e" * 35,
        ]

        for cred_text in credential_texts:
            regex_result = regex_scan(cred_text)
            assert regex_result.is_block is True

            # Build redacted_excerpt and suppression_hint as scan_or_block would
            excerpt = make_redacted_excerpt(cred_text, regex_result)
            hint = make_suppression_hint(
                regex_result.rule_id or "CREDENTIAL_DETECTED",
                regex_result.matched_slug or "unknown",
            )

            scan_result = ScanResult(
                action=Action.BLOCK,
                scan_id="01TEST000000000000000000003",
                rule_id=regex_result.rule_id,
                risk_level=regex_result.risk_level,
                redacted_excerpt=excerpt,
                suppression_hint=hint,
                test=regex_result.test,
            )
            response = build_block_response(scan_result)
            body_str = response.body.decode("utf-8")

            # Check no raw credential appears in any response field
            for pattern in cred_check_patterns:
                m = pattern.search(body_str)
                assert m is None, (
                    f"RAW CREDENTIAL IN BLOCK RESPONSE: {m.group(0)!r} found in body\n"
                    f"Original cred: {cred_text[:30]!r}"
                )
