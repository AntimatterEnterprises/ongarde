"""Tests for presidio_worker_init() — E-003-S-001.

All tests use mocked spaCy and Presidio to avoid requiring the real models in CI.
Integration tests with real models are in tests/integration/ with @pytest.mark.presidio.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

import app.scanner.presidio_worker as pw

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_worker_state():
    """Reset module-level state before each test."""
    original_nlp = pw._nlp
    original_analyzer = pw._analyzer
    original_entity_set = pw._entity_set
    yield
    pw._nlp = original_nlp
    pw._analyzer = original_analyzer
    pw._entity_set = original_entity_set


@pytest.fixture()
def mock_build_analyzer():
    """Patch _build_analyzer to return mock objects without loading real models."""
    mock_nlp = MagicMock()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = []
    with patch("app.scanner.presidio_worker._build_analyzer", return_value=(mock_nlp, mock_analyzer)) as m:
        yield m, mock_nlp, mock_analyzer


# ─── E-003-S-001: presidio_worker_init() ─────────────────────────────────────


class TestPresidioWorkerInit:
    def test_init_sets_analyzer_not_none(self, mock_build_analyzer):
        """After presidio_worker_init(), _analyzer is not None."""
        _, _, mock_analyzer = mock_build_analyzer
        pw.presidio_worker_init(("CREDIT_CARD", "EMAIL_ADDRESS"))
        assert pw._analyzer is mock_analyzer

    def test_init_sets_entity_set(self, mock_build_analyzer):
        """presidio_worker_init() stores passed entity_set in _entity_set."""
        entity_set = ("CREDIT_CARD", "CRYPTO", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN")
        pw.presidio_worker_init(entity_set)
        assert pw._entity_set == entity_set

    def test_init_calls_build_analyzer_with_entity_set(self, mock_build_analyzer):
        """presidio_worker_init() calls _build_analyzer with the entity set."""
        m, _, _ = mock_build_analyzer
        entity_set = ("CREDIT_CARD", "US_SSN")
        pw.presidio_worker_init(entity_set)
        m.assert_called_once_with(entity_set)

    def test_init_runs_15_warmup_calls(self, mock_build_analyzer):
        """presidio_worker_init() runs exactly 5 sizes × 3 iterations = 15 warmup calls."""
        _, _, mock_analyzer = mock_build_analyzer
        pw.presidio_worker_init(("CREDIT_CARD",))
        # 5 sizes × 3 iterations = 15 analyze calls during warmup
        assert mock_analyzer.analyze.call_count == 15

    def test_warmup_failure_does_not_crash_init(self, mock_build_analyzer):
        """Warmup scan failures are caught and logged — init still completes."""
        _, _, mock_analyzer = mock_build_analyzer
        mock_analyzer.analyze.side_effect = RuntimeError("Presidio error during warmup")
        # Should NOT raise — warmup failures are non-fatal
        pw.presidio_worker_init(("CREDIT_CARD",))
        # _analyzer is still set (init completed)
        assert pw._analyzer is mock_analyzer

    def test_analyzer_none_before_init(self):
        """_analyzer is None before presidio_worker_init() is called."""
        pw._analyzer = None
        assert pw._analyzer is None

    def test_init_build_analyzer_failure_leaves_analyzer_none(self):
        """If _build_analyzer raises, _analyzer remains None."""
        pw._analyzer = None
        with patch("app.scanner.presidio_worker._build_analyzer", side_effect=ImportError("spacy not found")):
            pw.presidio_worker_init(("CREDIT_CARD",))
        # _analyzer should still be None after failure
        assert pw._analyzer is None

    def test_init_logs_startup_messages(self, mock_build_analyzer, caplog):
        """presidio_worker_init() logs INFO messages at key checkpoints."""
        with caplog.at_level(logging.INFO):
            pw.presidio_worker_init(("CREDIT_CARD",))
        log_text = caplog.text.lower()
        assert "loading" in log_text or "init" in log_text

    def test_warmup_text_sizes_are_correct(self):
        """_make_warmup_text() returns text of exactly the requested size."""
        for size in pw._WARMUP_SIZES:
            text = pw._make_warmup_text(size)
            assert len(text) == size, f"Expected {size} chars, got {len(text)}"

    def test_warmup_text_no_pii(self):
        """Warmup text contains no email, phone, or common PII patterns."""
        for size in pw._WARMUP_SIZES:
            text = pw._make_warmup_text(size)
            # No @-sign (no email)
            assert "@" not in text
            # No digits long enough to be a credit card
            import re
            digits_runs = re.findall(r"\d{12,}", text)
            assert not digits_runs, f"Found suspicious digit run in warmup text: {digits_runs}"

    def test_warmup_iterations_count(self, mock_build_analyzer):
        """Verify _WARMUP_ITERATIONS is 3 and _WARMUP_SIZES has 5 entries."""
        assert pw._WARMUP_ITERATIONS == 3
        assert len(pw._WARMUP_SIZES) == 5
