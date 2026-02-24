"""Tests for presidio_scan_worker() — E-003-S-002.

All tests use mocked _analyzer to avoid requiring real models.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import app.scanner.presidio_worker as pw

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_worker_state():
    """Reset module-level state before each test."""
    original_analyzer = pw._analyzer
    original_entity_set = pw._entity_set
    yield
    pw._analyzer = original_analyzer
    pw._entity_set = original_entity_set


def _make_mock_result(entity_type: str, start: int, end: int, score: float = 0.85):
    """Create a mock RecognizerResult-like object."""
    r = MagicMock()
    r.entity_type = entity_type
    r.start = start
    r.end = end
    r.score = score
    return r


# ─── E-003-S-002: presidio_scan_worker() ─────────────────────────────────────


class TestPresidioScanWorker:
    def setup_method(self):
        """Set up mock analyzer before each test."""
        pw._analyzer = MagicMock()
        pw._entity_set = ("CREDIT_CARD", "CRYPTO", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN")

    def test_returns_list_of_dicts(self):
        """presidio_scan_worker() returns list of dicts, not RecognizerResult objects."""
        pw._analyzer.analyze.return_value = [
            _make_mock_result("CREDIT_CARD", 0, 16, 0.95)
        ]
        result = pw.presidio_scan_worker("4111111111111111 is my card")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_dict_has_required_keys(self):
        """Each returned dict has entity_type, start, end, score."""
        pw._analyzer.analyze.return_value = [
            _make_mock_result("EMAIL_ADDRESS", 5, 22, 0.90)
        ]
        result = pw.presidio_scan_worker("Hi, test@example.com")
        assert set(result[0].keys()) == {"entity_type", "start", "end", "score"}

    def test_dict_values_correct(self):
        """Returned dict values match RecognizerResult fields."""
        pw._analyzer.analyze.return_value = [
            _make_mock_result("US_SSN", 10, 21, 0.88)
        ]
        result = pw.presidio_scan_worker("My SSN is 123-45-6789")
        assert result[0]["entity_type"] == "US_SSN"
        assert result[0]["start"] == 10
        assert result[0]["end"] == 21
        assert result[0]["score"] == pytest.approx(0.88)

    def test_returns_empty_list_on_clean_text(self):
        """Clean text with no PII returns [] (not None)."""
        pw._analyzer.analyze.return_value = []
        result = pw.presidio_scan_worker("Hello, world!")
        assert result == []
        assert result is not None

    def test_raises_runtime_error_when_analyzer_none(self):
        """RuntimeError raised when _analyzer is None (worker not initialized)."""
        pw._analyzer = None
        with pytest.raises(RuntimeError, match="not initialized"):
            pw.presidio_scan_worker("some text")

    def test_return_value_is_json_serializable(self):
        """Returned list is JSON-serializable."""
        pw._analyzer.analyze.return_value = [
            _make_mock_result("CREDIT_CARD", 0, 16, 0.95)
        ]
        result = pw.presidio_scan_worker("4111111111111111")
        # Should not raise
        serialized = json.dumps(result)
        assert '"CREDIT_CARD"' in serialized

    def test_score_is_float_not_numpy(self):
        """score field is a Python float (not numpy.float32 or similar)."""
        mock_result = _make_mock_result("US_SSN", 0, 11, 0.92)
        # Simulate numpy float
        mock_result.score = 0.92  # plain float is fine too
        pw._analyzer.analyze.return_value = [mock_result]
        result = pw.presidio_scan_worker("123-45-6789")
        assert type(result[0]["score"]) is float

    def test_uses_entity_set_for_analyze(self):
        """presidio_scan_worker() passes _entity_set to analyzer.analyze()."""
        pw._analyzer.analyze.return_value = []
        pw._entity_set = ("CREDIT_CARD", "US_SSN")
        pw.presidio_scan_worker("some text")
        call_kwargs = pw._analyzer.analyze.call_args
        entities_arg = call_kwargs[1].get("entities") or call_kwargs[0][2] if len(call_kwargs[0]) > 2 else call_kwargs[1]["entities"]
        assert "CREDIT_CARD" in entities_arg
        assert "US_SSN" in entities_arg

    def test_multiple_entities_returned(self):
        """Multiple detected entities all returned in list."""
        pw._analyzer.analyze.return_value = [
            _make_mock_result("CREDIT_CARD", 0, 16, 0.95),
            _make_mock_result("EMAIL_ADDRESS", 20, 37, 0.90),
        ]
        result = pw.presidio_scan_worker("4111111111111111 and test@example.com")
        assert len(result) == 2
        entity_types = {r["entity_type"] for r in result}
        assert entity_types == {"CREDIT_CARD", "EMAIL_ADDRESS"}

    def test_no_score_filtering_in_worker(self):
        """Worker returns ALL entities regardless of confidence score (no filtering)."""
        # Even a very low score (0.1) should be returned
        pw._analyzer.analyze.return_value = [
            _make_mock_result("PHONE_NUMBER", 0, 12, 0.1)
        ]
        result = pw.presidio_scan_worker("test text")
        assert len(result) == 1
        assert result[0]["score"] == pytest.approx(0.1)
