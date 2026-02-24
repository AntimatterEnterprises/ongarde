"""ReDoS Audit Gate — CI Required Step.

Re2 uses linear-time finite automata. Patterns rejected by re2.compile()
would have exponential worst-case runtime under Python's stdlib ``re`` engine.
CI rejection of these patterns is the ReDoS defence.

Any PR adding a new pattern to definitions.py MUST pass this gate.
If re2.compile() raises re2.error for any pattern, this test fails and the PR is blocked.

Architecture reference: architecture.md §2.1, §10.1 (security requirements)
Story: E-002-S-006
"""

from __future__ import annotations

import pytest
import re2

from app.scanner.definitions import (
    ALL_PATTERNS,
    CREDENTIAL_PATTERNS,
    DANGEROUS_COMMAND_PATTERNS,
    PII_FAST_PATH_PATTERNS,
    PROMPT_INJECTION_PATTERNS,
)

# The re2._Regexp type — the compiled pattern type returned by re2.compile()
# Note: re2.Pattern does not exist in this version of google-re2 (1.1.x)
# The actual compiled type is re2._Regexp. The ReDoS gate verifies patterns
# are valid re2 objects that can execute searches without error.
_Re2PatternType = type(re2.compile(r'test'))


ALL_GROUPS: dict = {
    "CREDENTIAL_PATTERNS": CREDENTIAL_PATTERNS,
    "DANGEROUS_COMMAND_PATTERNS": DANGEROUS_COMMAND_PATTERNS,
    "PROMPT_INJECTION_PATTERNS": PROMPT_INJECTION_PATTERNS,
    "PII_FAST_PATH_PATTERNS": PII_FAST_PATH_PATTERNS,
}


@pytest.mark.parametrize(
    "group_name,entry",
    [
        (group_name, entry)
        for group_name, entries in ALL_GROUPS.items()
        for entry in entries
    ],
    ids=[
        f"{group_name}::{entry.rule_id}::{entry.slug}"
        for group_name, entries in ALL_GROUPS.items()
        for entry in entries
    ],
)
def test_pattern_is_re2_safe(group_name: str, entry: object) -> None:
    """Each pattern must have been compiled by re2 without error at module load.

    This test verifies the pattern object is a valid compiled re2 pattern.
    A re2.error at compile time (in definitions.py) would prevent import entirely,
    so this test also implicitly validates that definitions.py imported cleanly.

    Re2's linear-time guarantee means any pattern that compiles under re2
    cannot exhibit ReDoS behaviour — this is the CI gate for that guarantee.
    """
    assert isinstance(entry.pattern, _Re2PatternType), (  # type: ignore[attr-defined]
        f"[{group_name}] Pattern for {entry.rule_id!r} (slug={entry.slug!r}) "  # type: ignore[attr-defined]
        f"is not a compiled re2 pattern — got {type(entry.pattern)}. "  # type: ignore[attr-defined]
        "Did re2.compile() fail silently? Note: re2.Pattern does not exist in re2 1.1.x; "
        "compiled type is re2._Regexp."
    )
    # Verify the pattern can execute a search (tests runtime validity beyond compile)
    try:
        entry.pattern.search("test input for re2 safety validation")  # type: ignore[attr-defined]
    except re2.error as e:
        pytest.fail(
            f"[{group_name}] Pattern for {entry.rule_id!r} (slug={entry.slug!r}) "  # type: ignore[attr-defined]
            f"raises re2.error on search: {e}"
        )


def test_all_patterns_compiled_at_module_load() -> None:
    """ALL_PATTERNS must be populated (non-empty).

    Verifies definitions.py loaded successfully and patterns were compiled at
    import time (not deferred). Failure here indicates a compilation error in
    definitions.py that may have been swallowed silently.
    """
    assert len(ALL_PATTERNS) >= 75, (
        f"Expected >= 75 total patterns, got {len(ALL_PATTERNS)}. "
        "Check definitions.py for compilation errors."
    )


def test_no_bare_import_re_in_scanner_modules() -> None:
    """CI lint gate: no bare 'import re' in any app/scanner/ file (AC-E002-08)."""
    import pathlib
    import subprocess

    project_root = pathlib.Path(__file__).parent.parent.parent
    result = subprocess.run(
        ["grep", "-rn", r"^import re$|^from re import|^import re ", "app/scanner/"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
    )
    assert result.returncode != 0, (
        f"LINT GATE FAILURE: bare 'import re' found in app/scanner/:\n{result.stdout}"
    )


def test_no_scan_request_imported_outside_safe_scan() -> None:
    """scan_request must only be called from safe_scan.py (AC-S006-06)."""
    import pathlib
    import subprocess

    project_root = pathlib.Path(__file__).parent.parent.parent
    # Check that scan_request is not imported from outside safe_scan.py
    result = subprocess.run(
        ["grep", "-rn", "from app.scanner.engine import scan_request", "app/"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
    )
    if result.returncode == 0:
        # Filter out safe_scan.py — it's the only allowed importer
        illegal_imports = [
            line for line in result.stdout.splitlines()
            if "safe_scan.py" not in line
        ]
        assert not illegal_imports, (
            "scan_request imported outside safe_scan.py:\n" + "\n".join(illegal_imports)
        )


def test_each_group_has_minimum_patterns() -> None:
    """Regression guard: each pattern group must have minimum required patterns."""
    assert len(CREDENTIAL_PATTERNS) >= 20, (
        f"CREDENTIAL_PATTERNS has only {len(CREDENTIAL_PATTERNS)} entries — need >= 20"
    )
    assert len(DANGEROUS_COMMAND_PATTERNS) >= 28, (
        f"DANGEROUS_COMMAND_PATTERNS has only {len(DANGEROUS_COMMAND_PATTERNS)} entries — need >= 28"
    )
    assert len(PROMPT_INJECTION_PATTERNS) >= 20, (
        f"PROMPT_INJECTION_PATTERNS has only {len(PROMPT_INJECTION_PATTERNS)} entries — need >= 20"
    )
    assert len(PII_FAST_PATH_PATTERNS) >= 5, (
        f"PII_FAST_PATH_PATTERNS has only {len(PII_FAST_PATH_PATTERNS)} entries — need >= 5"
    )
