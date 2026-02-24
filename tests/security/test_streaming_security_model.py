"""Security documentation compliance tests — E-004-S-006, AC-E004-08.

These tests assert that the streaming security model documentation EXISTS and
contains the MANDATORY limitation text.

AC-E004-08 is a non-negotiable sprint gate:
  No product copy may claim zero malicious tokens reach the agent for streaming mode.
  The "up to 1 window" limitation must be explicitly stated in both README.md and
  docs/STREAMING_SECURITY_MODEL.md.

These tests prevent any future PR from removing the mandatory limitation text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── Project root (two levels up from tests/security/) ────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent


# ─── Test: Security model document existence ─────────────────────────────────

class TestSecurityModelDocExists:

    def test_streaming_security_model_doc_exists(self):
        """AC-E004-08: docs/STREAMING_SECURITY_MODEL.md must exist."""
        doc_path = PROJECT_ROOT / "docs" / "STREAMING_SECURITY_MODEL.md"
        assert doc_path.exists(), (
            f"MANDATORY: docs/STREAMING_SECURITY_MODEL.md not found at {doc_path}. "
            "AC-E004-08 requires this document before E-004 sprint close."
        )

    def test_security_model_doc_is_non_empty(self):
        """The security model doc must not be empty."""
        doc_path = PROJECT_ROOT / "docs" / "STREAMING_SECURITY_MODEL.md"
        if not doc_path.exists():
            pytest.skip("STREAMING_SECURITY_MODEL.md not found")
        content = doc_path.read_text(encoding="utf-8")
        assert len(content) > 100, "STREAMING_SECURITY_MODEL.md is too short to contain valid content"


# ─── Test: AC-E004-08 — mandatory limitation text ────────────────────────────

class TestStreamingLimitationDocumented:

    def test_security_model_contains_window_size(self):
        """AC-E004-08: 512-character window must be mentioned in the security model doc."""
        doc_path = PROJECT_ROOT / "docs" / "STREAMING_SECURITY_MODEL.md"
        if not doc_path.exists():
            pytest.skip("STREAMING_SECURITY_MODEL.md not found")
        content = doc_path.read_text(encoding="utf-8")
        assert "512" in content, (
            "STREAMING_SECURITY_MODEL.md must mention '512' (the window size in chars)"
        )

    def test_security_model_contains_token_count(self):
        """AC-E004-08: ≈128 tokens limitation must be mentioned."""
        doc_path = PROJECT_ROOT / "docs" / "STREAMING_SECURITY_MODEL.md"
        if not doc_path.exists():
            pytest.skip("STREAMING_SECURITY_MODEL.md not found")
        content = doc_path.read_text(encoding="utf-8")
        assert "128" in content, (
            "STREAMING_SECURITY_MODEL.md must mention '128' (≈128 tokens per window)"
        )

    def test_security_model_contains_may_reach_agent(self):
        """AC-E004-08: 'may reach the agent' limitation must be explicitly stated."""
        doc_path = PROJECT_ROOT / "docs" / "STREAMING_SECURITY_MODEL.md"
        if not doc_path.exists():
            pytest.skip("STREAMING_SECURITY_MODEL.md not found")
        content = doc_path.read_text(encoding="utf-8").lower()
        assert "may reach the agent" in content, (
            "STREAMING_SECURITY_MODEL.md must state that content 'may reach the agent' "
            "(AC-E004-08 mandatory limitation text)"
        )

    def test_readme_contains_streaming_limitation(self):
        """AC-E004-08: README.md must contain the streaming limitation section."""
        readme_path = PROJECT_ROOT / "README.md"
        if not readme_path.exists():
            pytest.skip("README.md not found")
        content = readme_path.read_text(encoding="utf-8").lower()
        assert "streaming limitation" in content, (
            "README.md must contain 'Streaming limitation' section (AC-E004-08 mandatory)"
        )

    def test_readme_mentions_window_limit(self):
        """AC-E004-08: README.md must mention the 512-character window."""
        readme_path = PROJECT_ROOT / "README.md"
        if not readme_path.exists():
            pytest.skip("README.md not found")
        content = readme_path.read_text(encoding="utf-8")
        assert "512" in content, (
            "README.md must mention '512' (streaming window size — AC-E004-08)"
        )

    def test_readme_does_not_claim_absolute_streaming_protection(self):
        """AC-E004-08: README.md must NOT claim zero malicious content for streaming."""
        readme_path = PROJECT_ROOT / "README.md"
        if not readme_path.exists():
            pytest.skip("README.md not found")
        content = readme_path.read_text(encoding="utf-8").lower()

        # Forbidden phrases that would constitute false absolute-guarantee claims
        forbidden_phrases = [
            "zero malicious tokens",
            "no malicious content will reach",
            "complete streaming protection",
            "streaming provides absolute",
            "absolute streaming security guarantee",
        ]
        for phrase in forbidden_phrases:
            assert phrase not in content, (
                f"README.md contains a false absolute-guarantee claim: '{phrase}'. "
                f"AC-E004-08 prohibits claiming zero malicious tokens for streaming."
            )

    def test_security_model_contains_strict_mode_stub_notice(self):
        """AC-E004-10: Security model doc must mention strict_mode stub."""
        doc_path = PROJECT_ROOT / "docs" / "STREAMING_SECURITY_MODEL.md"
        if not doc_path.exists():
            pytest.skip("STREAMING_SECURITY_MODEL.md not found")
        content = doc_path.read_text(encoding="utf-8")
        assert "strict_mode" in content, (
            "STREAMING_SECURITY_MODEL.md must document the strict_mode configuration key"
        )


# ─── Test: Strict_mode warning is in config.py ───────────────────────────────

class TestStrictModeInCode:

    def test_config_py_contains_strict_mode_warning(self):
        """config.py must contain the strict_mode warning string (AC-E004-10)."""
        config_path = PROJECT_ROOT / "app" / "config.py"
        assert config_path.exists(), "app/config.py not found"
        content = config_path.read_text(encoding="utf-8")
        assert "strict_mode is not implemented in v1" in content, (
            "app/config.py must contain the strict_mode warning string: "
            "'strict_mode is not implemented in v1 — ignored'"
        )

    def test_streaming_security_model_referenced_in_config_example(self):
        """config.yaml.example must reference the streaming security model doc."""
        example_path = PROJECT_ROOT / ".ongarde" / "config.yaml.example"
        if not example_path.exists():
            pytest.skip(".ongarde/config.yaml.example not found")
        content = example_path.read_text(encoding="utf-8")
        assert "strict_mode" in content, (
            ".ongarde/config.yaml.example must contain strict_mode field"
        )
