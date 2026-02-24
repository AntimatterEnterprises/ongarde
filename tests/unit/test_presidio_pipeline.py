"""Tests for the Presidio scan pipeline — E-003-S-005, E-003-S-006.

Tests for scan_request() routing, _presidio_sync_scan(), _presidio_advisory_scan(),
_make_presidio_block_result(), and asyncio timeout enforcement.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.scanner.engine as engine
from app.models.scan import Action, RiskLevel, ScanResult
from app.scanner.engine import (
    _make_presidio_block_result,
    _presidio_advisory_scan,
    _presidio_sync_scan,
    scan_request,
    update_calibration,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_engine_state():
    """Reset PRESIDIO_SYNC_CAP and PRESIDIO_TIMEOUT_S to defaults before each test."""
    original_cap = engine.PRESIDIO_SYNC_CAP
    original_timeout = engine.PRESIDIO_TIMEOUT_S
    yield
    engine.PRESIDIO_SYNC_CAP = original_cap
    engine.PRESIDIO_TIMEOUT_S = original_timeout


@pytest.fixture()
def mock_pool():
    return MagicMock(spec=ProcessPoolExecutor)


def _make_entity(entity_type: str, start: int = 0, end: int = 10, score: float = 0.9):
    return {"entity_type": entity_type, "start": start, "end": end, "score": score}


# ─── E-003-S-005: scan_request() routing ─────────────────────────────────────


class TestScanRequestRouting:
    @pytest.mark.asyncio
    async def test_scan_request_no_pool_allows(self):
        """scan_request() with scan_pool=None → ALLOW (regex-only path)."""
        result = await scan_request(
            text="Hello, world!",
            scan_pool=None,
            scan_id="test-scan-1",
            audit_context={},
        )
        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_scan_request_presidio_block_on_pii(self, mock_pool):
        """scan_request() with PII text and entities returned → BLOCK."""
        engine.PRESIDIO_SYNC_CAP = 500

        with patch("app.scanner.engine._presidio_sync_scan") as mock_sync:
            mock_sync.return_value = ScanResult(
                action=Action.BLOCK,
                scan_id="test-scan-3",
                rule_id="PRESIDIO_CREDIT_CARD",
                risk_level=RiskLevel.HIGH,
            )
            result = await scan_request(
                text="My card is 4111",
                scan_pool=mock_pool,
                scan_id="test-scan-3",
                audit_context={},
            )

        assert result.action == Action.BLOCK
        assert result.rule_id == "PRESIDIO_CREDIT_CARD"

    @pytest.mark.asyncio
    async def test_scan_request_presidio_allow_on_clean(self, mock_pool):
        """scan_request() with clean text, pool available → ALLOW after Presidio."""
        engine.PRESIDIO_SYNC_CAP = 500

        with patch("app.scanner.engine._presidio_sync_scan") as mock_sync:
            mock_sync.return_value = ScanResult(action=Action.ALLOW, scan_id="scan-4")
            result = await scan_request(
                text="The weather is nice today.",
                scan_pool=mock_pool,
                scan_id="scan-4",
                audit_context={},
            )

        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_scan_request_advisory_when_text_above_sync_cap(self, mock_pool):
        """Text > PRESIDIO_SYNC_CAP → advisory task scheduled, sync returns ALLOW."""
        engine.PRESIDIO_SYNC_CAP = 5  # Very small cap

        with patch("asyncio.create_task") as mock_task:
            result = await scan_request(
                text="This text is longer than 5 characters",
                scan_pool=mock_pool,
                scan_id="scan-5",
                audit_context={},
            )

        # Sync path returns ALLOW (no blocking in advisory mode)
        assert result.action == Action.ALLOW
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_request_sync_cap_zero_goes_advisory(self, mock_pool):
        """PRESIDIO_SYNC_CAP=0 (minimal tier) → all inputs go advisory, never sync."""
        engine.PRESIDIO_SYNC_CAP = 0

        with patch("app.scanner.engine._presidio_sync_scan") as mock_sync, \
             patch("asyncio.create_task"):
            result = await scan_request(
                text="short",
                scan_pool=mock_pool,
                scan_id="scan-6",
                audit_context={},
            )

        # Sync scan never called when PRESIDIO_SYNC_CAP=0
        mock_sync.assert_not_called()
        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_scan_request_sync_cap_zero_regex_still_runs(self, mock_pool):
        """AC-CALIB-008: Regex fast-path runs regardless of PRESIDIO_SYNC_CAP."""
        engine.PRESIDIO_SYNC_CAP = 0

        with patch("app.scanner.engine.regex_scan") as mock_regex, \
             patch("asyncio.create_task"):
            mock_regex.return_value = MagicMock(is_block=False)
            await scan_request("text", scan_pool=mock_pool, scan_id="s", audit_context={})

        mock_regex.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_request_calls_sync_for_text_at_exact_sync_cap(self, mock_pool):
        """text exactly at PRESIDIO_SYNC_CAP → sync path (not advisory)."""
        engine.PRESIDIO_SYNC_CAP = 10

        with patch("app.scanner.engine._presidio_sync_scan") as mock_sync:
            mock_sync.return_value = ScanResult(action=Action.ALLOW, scan_id="s")
            await scan_request(
                text="1234567890",  # exactly 10 chars = sync_cap
                scan_pool=mock_pool,
                scan_id="s",
                audit_context={},
            )

        mock_sync.assert_called_once()


# ─── E-003-S-005: _presidio_sync_scan() ──────────────────────────────────────


class TestPresidioSyncScan:
    @pytest.mark.asyncio
    async def test_sync_scan_block_on_entities(self, mock_pool):
        """_presidio_sync_scan() returns BLOCK when entities are detected."""
        engine.PRESIDIO_TIMEOUT_S = 0.5

        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(return_value=[_make_entity("CREDIT_CARD", 0, 16, 0.95)])):
            result = await _presidio_sync_scan(
                text="4111111111111111",
                scan_pool=mock_pool,
                scan_id="sync-1",
            )

        assert result.action == Action.BLOCK
        assert result.rule_id == "PRESIDIO_CREDIT_CARD"

    @pytest.mark.asyncio
    async def test_sync_scan_allow_on_empty_entities(self, mock_pool):
        """_presidio_sync_scan() returns ALLOW when no entities detected."""
        engine.PRESIDIO_TIMEOUT_S = 0.5

        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(return_value=[])):
            result = await _presidio_sync_scan(
                text="Hello world",
                scan_pool=mock_pool,
                scan_id="sync-2",
            )

        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_sync_scan_passes_presidio_timeout_s_to_executor(self, mock_pool):
        """_presidio_sync_scan() passes PRESIDIO_TIMEOUT_S (not hard-coded) to executor."""
        engine.PRESIDIO_TIMEOUT_S = 0.035  # non-default value

        timeout_used = []

        async def capture_args(scan_pool, text, timeout_s):
            timeout_used.append(timeout_s)
            return []

        with patch("app.scanner.engine._run_presidio_in_executor", side_effect=capture_args):
            await _presidio_sync_scan("text", mock_pool, "sync-3")

        assert timeout_used[0] == pytest.approx(0.035)

    @pytest.mark.asyncio
    async def test_sync_scan_propagates_timeout_error(self, mock_pool):
        """asyncio.TimeoutError from _run_presidio_in_executor propagates out."""
        engine.PRESIDIO_TIMEOUT_S = 0.001

        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(asyncio.TimeoutError):
                await _presidio_sync_scan("text", mock_pool, "sync-4")

    @pytest.mark.asyncio
    async def test_sync_scan_propagates_runtime_error(self, mock_pool):
        """RuntimeError from uninitialized worker propagates out of sync scan."""
        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(side_effect=RuntimeError("worker not initialized"))):
            with pytest.raises(RuntimeError):
                await _presidio_sync_scan("text", mock_pool, "sync-5")


# ─── E-003-S-005: _presidio_advisory_scan() ──────────────────────────────────


class TestPresidioAdvisoryScan:
    @pytest.mark.asyncio
    async def test_advisory_scan_sets_true_when_entities_found(self, mock_pool):
        """Advisory scan sets advisory_pii_detected=True when entities found."""
        audit_ctx = {}
        engine.PRESIDIO_TIMEOUT_S = 0.5

        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(return_value=[_make_entity("PHONE_NUMBER", 0, 12)])):
            await _presidio_advisory_scan("call 415-555-0123", mock_pool, "adv-1", audit_ctx)

        assert audit_ctx["advisory_pii_detected"] is True
        assert "PHONE_NUMBER" in audit_ctx["advisory_entities"]

    @pytest.mark.asyncio
    async def test_advisory_scan_sets_false_on_clean_text(self, mock_pool):
        """Advisory scan sets advisory_pii_detected=False on clean text."""
        audit_ctx = {}
        engine.PRESIDIO_TIMEOUT_S = 0.5

        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(return_value=[])):
            await _presidio_advisory_scan("clean text", mock_pool, "adv-2", audit_ctx)

        assert audit_ctx["advisory_pii_detected"] is False

    @pytest.mark.asyncio
    async def test_advisory_scan_sets_none_on_timeout(self, mock_pool):
        """Advisory scan sets advisory_pii_detected=None on timeout (unknown)."""
        audit_ctx = {}

        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(side_effect=asyncio.TimeoutError)):
            await _presidio_advisory_scan("text", mock_pool, "adv-3", audit_ctx)

        assert audit_ctx["advisory_pii_detected"] is None

    @pytest.mark.asyncio
    async def test_advisory_scan_sets_none_on_exception(self, mock_pool):
        """Advisory scan sets advisory_pii_detected=None on error (unknown)."""
        audit_ctx = {}

        with patch("app.scanner.engine._run_presidio_in_executor",
                   new=AsyncMock(side_effect=RuntimeError("worker dead"))):
            await _presidio_advisory_scan("text", mock_pool, "adv-4", audit_ctx)

        assert audit_ctx["advisory_pii_detected"] is None

    @pytest.mark.asyncio
    async def test_advisory_scan_uses_3x_timeout(self, mock_pool):
        """Advisory timeout is 3× PRESIDIO_TIMEOUT_S."""
        engine.PRESIDIO_TIMEOUT_S = 0.040
        audit_ctx = {}

        timeout_used = []

        async def capture_args(scan_pool, text, timeout_s):
            timeout_used.append(timeout_s)
            return []

        with patch("app.scanner.engine._run_presidio_in_executor", side_effect=capture_args):
            await _presidio_advisory_scan("text", mock_pool, "adv-5", audit_ctx)

        assert timeout_used[0] == pytest.approx(0.040 * 3.0)


# ─── E-003-S-005: _make_presidio_block_result() ──────────────────────────────


class TestMakePresidioBlockResult:
    def test_rule_id_format(self):
        """rule_id is PRESIDIO_{ENTITY_TYPE}."""
        entities = [_make_entity("CREDIT_CARD", 0, 16, 0.95)]
        result = _make_presidio_block_result(entities, "4111111111111111", "r1")
        assert result.rule_id == "PRESIDIO_CREDIT_CARD"

    def test_rule_id_for_ssn(self):
        """rule_id is PRESIDIO_US_SSN for SSN detections."""
        entities = [_make_entity("US_SSN", 0, 11, 0.88)]
        result = _make_presidio_block_result(entities, "123-45-6789", "r2")
        assert result.rule_id == "PRESIDIO_US_SSN"

    def test_risk_level_is_high(self):
        """All Presidio detections use RiskLevel.HIGH."""
        entities = [_make_entity("US_SSN", 0, 11, 0.88)]
        result = _make_presidio_block_result(entities, "123-45-6789", "r-high")
        assert result.risk_level == RiskLevel.HIGH

    def test_action_is_block(self):
        """_make_presidio_block_result always returns BLOCK."""
        entities = [_make_entity("EMAIL_ADDRESS", 0, 15, 0.9)]
        result = _make_presidio_block_result(entities, "test@example.com", "r3")
        assert result.action == Action.BLOCK

    def test_selects_highest_score_entity(self):
        """Highest-confidence entity is selected as primary when multiple detected."""
        entities = [
            _make_entity("PHONE_NUMBER", 0, 12, 0.7),
            _make_entity("CREDIT_CARD", 20, 36, 0.95),  # highest score
        ]
        result = _make_presidio_block_result(entities, "x " * 50, "r4")
        assert result.rule_id == "PRESIDIO_CREDIT_CARD"

    def test_redacted_excerpt_contains_redacted_marker(self):
        """Redacted excerpt contains [REDACTED] for the matched span."""
        entities = [_make_entity("CREDIT_CARD", 0, 16, 0.95)]
        text = "4111111111111111 is my card"
        result = _make_presidio_block_result(entities, text, "r5")
        assert "[REDACTED]" in result.redacted_excerpt

    def test_suppression_hint_not_none(self):
        """Presidio BLOCK includes suppression_hint for allowlist."""
        entities = [_make_entity("EMAIL_ADDRESS", 0, 15, 0.9)]
        result = _make_presidio_block_result(entities, "test@example.com", "r6")
        assert result.suppression_hint is not None


# ─── E-003-S-006: Timeout enforcement ────────────────────────────────────────


class TestTimeoutEnforcement:
    @pytest.mark.asyncio
    async def test_presidio_timeout_uses_runtime_variable_not_hardcoded(self, mock_pool):
        """PRESIDIO_TIMEOUT_S is the module variable, not hard-coded 40ms."""
        engine.PRESIDIO_TIMEOUT_S = 0.055  # 55ms — non-default value

        timeout_used = []

        async def capture_args(scan_pool, text, timeout_s):
            timeout_used.append(timeout_s)
            return []

        with patch("app.scanner.engine._run_presidio_in_executor", side_effect=capture_args):
            await _presidio_sync_scan("text", mock_pool, "t1")

        assert timeout_used[0] == pytest.approx(0.055)
        assert timeout_used[0] != 0.040  # explicitly not hard-coded 40ms

    @pytest.mark.asyncio
    async def test_update_calibration_changes_presidio_timeout_s(self):
        """update_calibration() updates PRESIDIO_TIMEOUT_S and PRESIDIO_SYNC_CAP."""
        update_calibration(sync_cap=800, timeout_s=0.030)
        assert engine.PRESIDIO_TIMEOUT_S == pytest.approx(0.030)
        assert engine.PRESIDIO_SYNC_CAP == 800

    def test_presidio_timeout_max_lte_scanner_global_timeout(self):
        """PRESIDIO_TIMEOUT_MAX_S ≤ SCANNER_GLOBAL_TIMEOUT_S (invariant)."""
        from app.constants import PRESIDIO_TIMEOUT_MAX_S
        from app.scanner.safe_scan import SCANNER_GLOBAL_TIMEOUT_S
        assert PRESIDIO_TIMEOUT_MAX_S <= SCANNER_GLOBAL_TIMEOUT_S

    @pytest.mark.asyncio
    async def test_scan_request_timeout_error_propagates(self, mock_pool):
        """asyncio.TimeoutError from Presidio propagates through scan_request()."""
        engine.PRESIDIO_SYNC_CAP = 500
        engine.PRESIDIO_TIMEOUT_S = 0.001

        with patch("app.scanner.engine._presidio_sync_scan",
                   new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(asyncio.TimeoutError):
                await scan_request(
                    text="short",
                    scan_pool=mock_pool,
                    scan_id="t2",
                    audit_context={},
                )

    @pytest.mark.asyncio
    async def test_scanner_timeout_rule_id_from_safe_scan(self):
        """scan_or_block() with Presidio timeout → BLOCK with rule_id=SCANNER_TIMEOUT."""
        from app.scanner.safe_scan import scan_or_block
        engine.PRESIDIO_SYNC_CAP = 500

        with patch("app.scanner.engine._presidio_sync_scan",
                   new=AsyncMock(side_effect=asyncio.TimeoutError)), \
             patch("app.scanner.safe_scan.SCANNER_GLOBAL_TIMEOUT_S", 0.1):
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            result = await scan_or_block(
                content="short",
                scan_pool=mock_pool,
                scan_id="timeout-test",
                audit_context={},
            )

        assert result.action == Action.BLOCK
        assert result.rule_id == "SCANNER_TIMEOUT"
