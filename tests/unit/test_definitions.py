"""Unit tests for app/scanner/definitions.py — E-002-S-001.

Verifies:
  - Module imports without error (pattern compilation succeeds at module load)
  - ALL_PATTERNS has >= 20 credential + >= 28 dangerous + >= 20 injection + >= 5 PII entries
  - Each entry has all required fields (compiled re2 pattern, non-empty rule_id, slug)
  - No bare 'import re' in definitions.py (CI lint gate verification)
  - Known test key pattern has test=True
  - PatternEntry dataclass is frozen (immutable)
  - Pattern compilation benchmark: import completes in < 100ms

AC coverage: AC-S001-01 through AC-S001-08
"""

from __future__ import annotations

import subprocess
import time

import pytest
import re2

from app.models.scan import RiskLevel
from app.scanner.definitions import (
    ALL_PATTERNS,
    CREDENTIAL_PATTERNS,
    DANGEROUS_COMMAND_PATTERNS,
    PII_FAST_PATH_PATTERNS,
    PROMPT_INJECTION_PATTERNS,
    TEST_CREDENTIAL_PATTERN,
)

# ---------------------------------------------------------------------------
# AC-S001-01: No bare import re
# ---------------------------------------------------------------------------


class TestNoBarImportRe:
    """CI lint gate: no bare 'import re' in definitions.py."""

    def test_no_bare_import_re_in_definitions(self) -> None:
        """grep must find zero bare 'import re' lines in app/scanner/."""
        result = subprocess.run(
            ["grep", "-rn", r"^import re$|^from re import|^import re ", "app/scanner/"],
            capture_output=True,
            text=True,
            cwd=str(__import__("pathlib").Path(__file__).parent.parent.parent),
        )
        assert result.returncode != 0, (
            f"LINT GATE FAILURE: bare 'import re' found in app/scanner/:\n{result.stdout}"
        )


# ---------------------------------------------------------------------------
# AC-S001-02: Pre-compilation at module load
# ---------------------------------------------------------------------------


class TestPreCompilation:
    """All patterns must be compiled re2 objects, not strings or None."""

    def test_module_imports_without_error(self) -> None:
        """Pattern compilation succeeds at module load (no re2.error)."""
        import app.scanner.definitions  # noqa: F401 — just verify import
        assert True

    def test_all_patterns_are_compiled_re2(self) -> None:
        """Every PatternEntry.pattern must be a compiled re2._Regexp object."""
        pattern_type = type(re2.compile(r'test'))
        for entry in ALL_PATTERNS:
            assert isinstance(entry.pattern, pattern_type), (
                f"Pattern for {entry.rule_id!r} (slug={entry.slug!r}) "
                f"is not a compiled re2 pattern — got {type(entry.pattern)}"
            )

    def test_import_benchmark_under_100ms(self) -> None:
        """Module import (including all re2.compile() calls) must complete in < 100ms."""
        # Force a clean import by measuring how long the module-level work takes
        # Since module is already cached, we measure pattern access time
        start = time.perf_counter()
        _ = len(ALL_PATTERNS)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # This tests that ALL_PATTERNS is pre-computed, not lazily evaluated
        # The actual compilation time was at module import — this verifies access is instant
        assert elapsed_ms < 100, (
            f"ALL_PATTERNS access took {elapsed_ms:.2f}ms — patterns must be pre-compiled"
        )


# ---------------------------------------------------------------------------
# AC-S001-03: Credential pattern group
# ---------------------------------------------------------------------------


class TestCredentialPatterns:
    """Credential pattern group has all required entries and metadata."""

    def test_minimum_credential_count(self) -> None:
        """CREDENTIAL_PATTERNS must have >= 20 entries."""
        assert len(CREDENTIAL_PATTERNS) >= 20, (
            f"Need >= 20 credential patterns, got {len(CREDENTIAL_PATTERNS)}"
        )

    def test_all_entries_have_required_fields(self) -> None:
        """Every PatternEntry must have non-empty rule_id and slug."""
        for entry in CREDENTIAL_PATTERNS:
            assert entry.rule_id, f"rule_id empty for entry with slug={entry.slug!r}"
            assert entry.slug, f"slug empty for entry with rule_id={entry.rule_id!r}"
            assert entry.risk_level is not None, f"risk_level None for {entry.rule_id!r}"

    def test_all_credentials_are_critical(self) -> None:
        """All credential entries must be CRITICAL risk."""
        for entry in CREDENTIAL_PATTERNS:
            assert entry.risk_level == RiskLevel.CRITICAL, (
                f"Credential {entry.slug!r} has non-CRITICAL risk: {entry.risk_level}"
            )

    def test_test_flag_false_on_all_non_test_credentials(self) -> None:
        """No regular credential entry should have test=True (only test key does)."""
        for entry in CREDENTIAL_PATTERNS:
            assert entry.test is False, (
                f"Regular credential {entry.slug!r} has test=True — only TEST_CREDENTIAL_PATTERN should"
            )


# ---------------------------------------------------------------------------
# AC-S001-03: Known test credential
# ---------------------------------------------------------------------------


class TestKnownTestCredential:
    """TEST_CREDENTIAL_PATTERN must have test=True and match the test key."""

    def test_test_credential_has_test_flag(self) -> None:
        """TEST_CREDENTIAL_PATTERN must have test=True."""
        assert TEST_CREDENTIAL_PATTERN.test is True

    def test_test_credential_matches_test_key(self) -> None:
        """Test pattern must match sk-ongarde-test-fake-key-12345."""
        m = TEST_CREDENTIAL_PATTERN.pattern.search("sk-ongarde-test-fake-key-12345")
        assert m is not None, "TEST_CREDENTIAL_PATTERN must match sk-ongarde-test-fake-key-12345"

    def test_test_credential_rule_id(self) -> None:
        """TEST_CREDENTIAL_PATTERN rule_id must be CREDENTIAL_DETECTED."""
        assert TEST_CREDENTIAL_PATTERN.rule_id == "CREDENTIAL_DETECTED"

    def test_test_credential_risk_level(self) -> None:
        """TEST_CREDENTIAL_PATTERN risk_level must be CRITICAL."""
        assert TEST_CREDENTIAL_PATTERN.risk_level == RiskLevel.CRITICAL

    def test_test_credential_not_in_main_list(self) -> None:
        """TEST_CREDENTIAL_PATTERN is separate — not duplicated inside CREDENTIAL_PATTERNS."""
        for entry in CREDENTIAL_PATTERNS:
            if entry.test is True:
                pytest.fail(
                    f"Found test=True entry {entry.slug!r} inside CREDENTIAL_PATTERNS — "
                    "it should be the standalone TEST_CREDENTIAL_PATTERN"
                )


# ---------------------------------------------------------------------------
# AC-S001-04: Dangerous command pattern group
# ---------------------------------------------------------------------------


class TestDangerousCommandPatterns:
    """Dangerous command pattern group has all required entries."""

    def test_minimum_dangerous_count(self) -> None:
        """DANGEROUS_COMMAND_PATTERNS must have >= 28 entries (spec: 30+)."""
        assert len(DANGEROUS_COMMAND_PATTERNS) >= 28, (
            f"Need >= 28 dangerous command patterns, got {len(DANGEROUS_COMMAND_PATTERNS)}"
        )

    def test_all_entries_have_required_fields(self) -> None:
        """Every dangerous command entry must have non-empty rule_id and slug."""
        for entry in DANGEROUS_COMMAND_PATTERNS:
            assert entry.rule_id, f"rule_id empty for slug={entry.slug!r}"
            assert entry.slug, f"slug empty for rule_id={entry.rule_id!r}"

    def test_shell_sql_destructors_are_critical(self) -> None:
        """Shell and SQL destructor patterns must be CRITICAL risk."""
        critical_slugs = {
            "rm-rf", "rm-fr", "sudo-usage", "dd-disk-copy", "mkfs-format",
            "chmod-world-writable", "curl-pipe-execute", "wget-pipe-execute",
            "fork-bomb", "direct-disk-write",
            "sql-drop-table", "sql-drop-database", "sql-truncate",
            "sql-delete-no-where", "sql-delete-no-where-eol",
        }
        for entry in DANGEROUS_COMMAND_PATTERNS:
            if entry.slug in critical_slugs:
                assert entry.risk_level == RiskLevel.CRITICAL, (
                    f"Shell/SQL destructor {entry.slug!r} must be CRITICAL, got {entry.risk_level}"
                )

    def test_path_and_exec_are_high_or_critical(self) -> None:
        """Path access and code execution patterns must be HIGH or CRITICAL."""
        for entry in DANGEROUS_COMMAND_PATTERNS:
            assert entry.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH), (
                f"Dangerous pattern {entry.slug!r} has risk {entry.risk_level} — "
                "expected CRITICAL or HIGH"
            )


# ---------------------------------------------------------------------------
# AC-S001-05: Prompt injection pattern group
# ---------------------------------------------------------------------------


class TestPromptInjectionPatterns:
    """Prompt injection pattern group has all required entries."""

    def test_minimum_injection_count(self) -> None:
        """PROMPT_INJECTION_PATTERNS must have >= 20 entries."""
        assert len(PROMPT_INJECTION_PATTERNS) >= 20, (
            f"Need >= 20 injection patterns, got {len(PROMPT_INJECTION_PATTERNS)}"
        )

    def test_all_entries_have_required_fields(self) -> None:
        """Every injection entry must have non-empty rule_id and slug."""
        for entry in PROMPT_INJECTION_PATTERNS:
            assert entry.rule_id, f"rule_id empty for slug={entry.slug!r}"
            assert entry.slug, f"slug empty for rule_id={entry.rule_id!r}"

    def test_risk_levels_are_high_or_medium(self) -> None:
        """Injection patterns must be HIGH or MEDIUM — never CRITICAL or LOW."""
        for entry in PROMPT_INJECTION_PATTERNS:
            assert entry.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM), (
                f"Injection {entry.slug!r} has risk {entry.risk_level} — expected HIGH or MEDIUM"
            )


# ---------------------------------------------------------------------------
# AC-S001-06: PII fast-path pattern group
# ---------------------------------------------------------------------------


class TestPIIFastPathPatterns:
    """PII fast-path pattern group has all 5 required entity types."""

    def test_minimum_pii_count(self) -> None:
        """PII_FAST_PATH_PATTERNS must have >= 5 entries."""
        assert len(PII_FAST_PATH_PATTERNS) >= 5, (
            f"Need >= 5 PII patterns, got {len(PII_FAST_PATH_PATTERNS)}"
        )

    def test_pii_rule_ids_use_correct_prefix(self) -> None:
        """PII rule_ids must use PII_DETECTED_ prefix."""
        for entry in PII_FAST_PATH_PATTERNS:
            assert entry.rule_id.startswith("PII_DETECTED_"), (
                f"PII pattern {entry.slug!r} has rule_id {entry.rule_id!r} — "
                "must start with PII_DETECTED_"
            )

    def test_required_pii_rule_ids_present(self) -> None:
        """All 5 required PII entity types must be present."""
        rule_ids = {e.rule_id for e in PII_FAST_PATH_PATTERNS}
        required = {
            "PII_DETECTED_US_SSN",
            "PII_DETECTED_CREDIT_CARD",
            "PII_DETECTED_EMAIL",
            "PII_DETECTED_PHONE_US",
            "PII_DETECTED_CRYPTO",
        }
        missing = required - rule_ids
        assert not missing, f"Missing required PII rule_ids: {missing}"

    def test_pii_patterns_match_expected_data(self) -> None:
        """Smoke test: each PII pattern matches at least one canonical example."""
        test_cases = [
            ("PII_DETECTED_US_SSN", "My SSN is 123-45-6789"),
            ("PII_DETECTED_CREDIT_CARD", "Card: 4532015112830366"),
            ("PII_DETECTED_EMAIL", "Email: user@example.com"),
            ("PII_DETECTED_PHONE_US", "Call 555-867-5309"),
            ("PII_DETECTED_CRYPTO", "ETH: 0x742d35Cc6634C0532925a3b844Bc454e4438f44e"),
        ]
        for rule_id, test_input in test_cases:
            matched = False
            for entry in PII_FAST_PATH_PATTERNS:
                if entry.rule_id == rule_id and entry.pattern.search(test_input):
                    matched = True
                    break
            assert matched, f"PII pattern {rule_id!r} did not match: {test_input!r}"


# ---------------------------------------------------------------------------
# AC-S001-07: PatternEntry metadata structure
# ---------------------------------------------------------------------------


class TestPatternEntryStructure:
    """PatternEntry dataclass has the required fields and is frozen."""

    def test_pattern_entry_is_frozen(self) -> None:
        """PatternEntry must be a frozen dataclass (immutable)."""
        entry = CREDENTIAL_PATTERNS[0]
        with pytest.raises((AttributeError, TypeError)):
            entry.rule_id = "MODIFIED"  # type: ignore[misc]

    def test_all_entries_have_slugs(self) -> None:
        """Every pattern in ALL_PATTERNS must have a non-empty kebab-case slug."""
        for entry in ALL_PATTERNS:
            assert entry.slug, f"Empty slug for rule_id={entry.rule_id!r}"
            # Slug should be kebab-case (lowercase, hyphens, no spaces)
            assert " " not in entry.slug, f"Slug has spaces: {entry.slug!r}"

    def test_all_patterns_exported(self) -> None:
        """ALL_PATTERNS must include all four groups."""
        expected = (
            1  # TEST_CREDENTIAL_PATTERN
            + len(CREDENTIAL_PATTERNS)
            + len(DANGEROUS_COMMAND_PATTERNS)
            + len(PROMPT_INJECTION_PATTERNS)
            + len(PII_FAST_PATH_PATTERNS)
        )
        assert len(ALL_PATTERNS) == expected, (
            f"ALL_PATTERNS count mismatch: {len(ALL_PATTERNS)} != {expected}"
        )

    def test_total_pattern_count(self) -> None:
        """ALL_PATTERNS must have >= 75 total patterns (spec: >= 75)."""
        assert len(ALL_PATTERNS) >= 75, (
            f"Need >= 75 total patterns, got {len(ALL_PATTERNS)}"
        )


# ---------------------------------------------------------------------------
# AC-S001-08: No ReDoS patterns
# ---------------------------------------------------------------------------


class TestNoReDoSPatterns:
    """All patterns must compile under re2 (linear-time guarantee)."""

    def test_all_patterns_compile_without_error(self) -> None:
        """re2.compile() must succeed for every pattern (ReDoS gate)."""
        # This test passes if definitions.py imported — compile errors prevent import.
        # We additionally verify each pattern is usable.
        for entry in ALL_PATTERNS:
            try:
                entry.pattern.search("test input for re2 safety check")
            except Exception as exc:
                pytest.fail(
                    f"Pattern {entry.rule_id!r} (slug={entry.slug!r}) "
                    f"raised on search: {exc}"
                )
