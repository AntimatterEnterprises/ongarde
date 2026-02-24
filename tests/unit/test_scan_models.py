"""Unit tests for app/models/scan.py — E-001-S-006.

Tests the scan gate data models: Action, RiskLevel, ScanResult.
These models define the contract that E-002 and E-003 must fulfil.

AC coverage:
  AC-E001-06 (stub, items 5–7): ScanResult, Action, RiskLevel types defined
  DoD: app/models/scan.py exists with Action, RiskLevel, ScanResult dataclasses
"""

from __future__ import annotations

from app.models.scan import Action, RiskLevel, ScanResult

# ─── Action Enum ──────────────────────────────────────────────────────────────


class TestActionEnum:
    """Tests for the Action decision enum."""

    def test_action_allow_value(self) -> None:
        assert Action.ALLOW == "ALLOW"
        assert Action.ALLOW.value == "ALLOW"

    def test_action_block_value(self) -> None:
        assert Action.BLOCK == "BLOCK"
        assert Action.BLOCK.value == "BLOCK"

    def test_action_allow_suppressed_value(self) -> None:
        assert Action.ALLOW_SUPPRESSED == "ALLOW_SUPPRESSED"
        assert Action.ALLOW_SUPPRESSED.value == "ALLOW_SUPPRESSED"

    def test_action_is_str_enum(self) -> None:
        """Action values are strings for JSON serialisation compatibility."""
        assert isinstance(Action.ALLOW, str)
        assert isinstance(Action.BLOCK, str)
        assert isinstance(Action.ALLOW_SUPPRESSED, str)

    def test_action_equality_with_string(self) -> None:
        """Enum members compare equal to their string values (str Enum)."""
        assert Action.ALLOW == "ALLOW"
        assert Action.BLOCK == "BLOCK"

    def test_action_distinct_values(self) -> None:
        """All three action values are distinct — never confused."""
        assert Action.ALLOW != Action.BLOCK
        assert Action.ALLOW != Action.ALLOW_SUPPRESSED
        assert Action.BLOCK != Action.ALLOW_SUPPRESSED

    def test_action_all_three_members(self) -> None:
        members = {a.value for a in Action}
        assert members == {"ALLOW", "BLOCK", "ALLOW_SUPPRESSED"}


# ─── RiskLevel Enum ───────────────────────────────────────────────────────────


class TestRiskLevelEnum:
    """Tests for the RiskLevel classification enum."""

    def test_critical_value(self) -> None:
        assert RiskLevel.CRITICAL == "CRITICAL"

    def test_high_value(self) -> None:
        assert RiskLevel.HIGH == "HIGH"

    def test_medium_value(self) -> None:
        assert RiskLevel.MEDIUM == "MEDIUM"

    def test_low_value(self) -> None:
        assert RiskLevel.LOW == "LOW"

    def test_risk_level_is_str_enum(self) -> None:
        assert isinstance(RiskLevel.CRITICAL, str)
        assert isinstance(RiskLevel.LOW, str)

    def test_all_four_levels(self) -> None:
        levels = {r.value for r in RiskLevel}
        assert levels == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

    def test_levels_distinct(self) -> None:
        assert RiskLevel.CRITICAL != RiskLevel.HIGH
        assert RiskLevel.HIGH != RiskLevel.MEDIUM
        assert RiskLevel.MEDIUM != RiskLevel.LOW


# ─── ScanResult Dataclass ─────────────────────────────────────────────────────


class TestScanResult:
    """Tests for the ScanResult dataclass."""

    def test_allow_result_minimal(self) -> None:
        """Minimal ALLOW result — only required fields set."""
        result = ScanResult(action=Action.ALLOW, scan_id="01SCAN000000000000000000000")
        assert result.action == Action.ALLOW
        assert result.scan_id == "01SCAN000000000000000000000"
        assert result.rule_id is None
        assert result.risk_level is None
        assert result.redacted_excerpt is None
        assert result.suppression_hint is None
        assert result.test is False

    def test_block_result_full_fields(self) -> None:
        """BLOCK result with all optional fields populated."""
        result = ScanResult(
            action=Action.BLOCK,
            scan_id="01SCAN000000000000000000001",
            rule_id="CRED-001",
            risk_level=RiskLevel.CRITICAL,
            redacted_excerpt="sk-***REDACTED***",
            suppression_hint=None,
            test=False,
        )
        assert result.action == Action.BLOCK
        assert result.rule_id == "CRED-001"
        assert result.risk_level == RiskLevel.CRITICAL
        assert result.redacted_excerpt == "sk-***REDACTED***"
        assert result.suppression_hint is None
        assert result.test is False

    def test_test_flag(self) -> None:
        """test=True is accepted (for E-007 onboarding aha moment)."""
        result = ScanResult(action=Action.BLOCK, scan_id="s", test=True)
        assert result.test is True

    def test_allow_suppressed(self) -> None:
        """ALLOW_SUPPRESSED action is valid in the model."""
        result = ScanResult(
            action=Action.ALLOW_SUPPRESSED,
            scan_id="s",
            rule_id="SUPPRESSED-001",
        )
        assert result.action == Action.ALLOW_SUPPRESSED

    def test_scan_id_preserved(self) -> None:
        """scan_id is stored exactly as provided."""
        ulid = "01KJ0JRVHYA7KX32VPN5ZSCTMV"
        result = ScanResult(action=Action.ALLOW, scan_id=ulid)
        assert result.scan_id == ulid

    def test_suppression_hint_none_in_stub(self) -> None:
        """suppression_hint is None in E-001-S-006 (generated in E-002/E-009)."""
        result = ScanResult(action=Action.BLOCK, scan_id="s")
        assert result.suppression_hint is None

    def test_redacted_excerpt_does_not_contain_raw_creds(self) -> None:
        """Contract check: redacted_excerpt should never hold raw credentials.

        This test documents the contract — the model itself doesn't enforce it,
        but callers (E-002) must sanitise the excerpt before storing it.
        """
        # A well-formed redacted excerpt should mask the sensitive part.
        result = ScanResult(
            action=Action.BLOCK,
            scan_id="s",
            redacted_excerpt="sk-***REDACTED***",
        )
        # The excerpt should not contain a complete API key pattern.
        assert result.redacted_excerpt is not None
        assert "***" in result.redacted_excerpt  # some masking applied
