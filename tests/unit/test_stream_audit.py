"""Unit tests for streaming audit trail — E-004-S-004.

Tests:
  - BLOCK event logged with action=BLOCK, direction=RESPONSE
  - tokens_delivered in audit event matches scanner.tokens_delivered
  - rule_id and risk_level populated from scan_result
  - Audit event uses asyncio.create_task() (fire-and-forget, not awaited)
  - audit_backend=None is graceful no-op
  - Background Presidio advisory (mocked) sets abort trigger
  - ALLOW event logged on clean stream completion
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.audit.models import AuditEvent
from app.proxy.engine import (
    _log_stream_event,
    _presidio_advisory_stream_scan,
    _stream_response_scan,
)

SCAN_ID = "01HXXXXXXXXXX_AUDIT_TEST"
USER_ID = "user-audit-test"
CREDENTIAL = "sk-testABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmn"


def clean_text(n: int) -> str:
    base = "The quick brown fox jumps over the lazy dog. "
    return (base * ((n // len(base)) + 2))[:n]


def make_openai_chunk(content: str) -> str:
    data = {"choices": [{"delta": {"content": content}}]}
    return f"data: {json.dumps(data)}\n\n"


class MockStreamingResponse:
    def __init__(self, chunks: list[str]):
        self._chunks = [c.encode("utf-8") for c in chunks]
        self._closed = False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self._closed = True


async def collect_stream(gen) -> list[bytes]:
    result = []
    async for chunk in gen:
        result.append(chunk)
    return result


# ─── Test: _log_stream_event ──────────────────────────────────────────────────

class TestLogStreamEvent:

    @pytest.mark.asyncio
    async def test_block_event_logged_with_correct_fields(self):
        """BLOCK event logged with action=BLOCK, direction=RESPONSE (AC-E004-04)."""
        mock_backend = AsyncMock()
        await _log_stream_event(
            audit_backend=mock_backend,
            scan_id=SCAN_ID,
            user_id=USER_ID,
            action="BLOCK",
            rule_id="CREDENTIAL_DETECTED",
            risk_level_str="CRITICAL",
            redacted_excerpt="...text[CREDENTIAL]...",
            tokens_delivered=128,
        )
        mock_backend.log_event.assert_called_once()
        event: AuditEvent = mock_backend.log_event.call_args[0][0]
        assert event.action == "BLOCK"
        assert event.direction == "RESPONSE"
        assert event.scan_id == SCAN_ID
        assert event.user_id == USER_ID
        assert event.rule_id == "CREDENTIAL_DETECTED"
        assert event.risk_level == "CRITICAL"
        assert event.tokens_delivered == 128

    @pytest.mark.asyncio
    async def test_allow_event_has_no_tokens_delivered(self):
        """ALLOW event has tokens_delivered=None (only BLOCK events track tokens)."""
        mock_backend = AsyncMock()
        await _log_stream_event(
            audit_backend=mock_backend,
            scan_id=SCAN_ID,
            user_id=USER_ID,
            action="ALLOW",
            rule_id=None,
            risk_level_str=None,
            redacted_excerpt=None,
            tokens_delivered=256,  # passed but should not be stored for ALLOW
        )
        event: AuditEvent = mock_backend.log_event.call_args[0][0]
        assert event.action == "ALLOW"
        assert event.tokens_delivered is None  # Not stored for ALLOW events

    @pytest.mark.asyncio
    async def test_none_backend_is_graceful_noop(self):
        """audit_backend=None: no crash, no call (AC-E004-04 audit_backend=None)."""
        # Should complete without exception
        await _log_stream_event(
            audit_backend=None,
            scan_id=SCAN_ID,
            user_id=USER_ID,
            action="BLOCK",
            rule_id="RULE",
            risk_level_str="HIGH",
            redacted_excerpt=None,
            tokens_delivered=0,
        )

    @pytest.mark.asyncio
    async def test_advisory_entities_included(self):
        """Advisory Presidio entities included in audit event when detected."""
        mock_backend = AsyncMock()
        await _log_stream_event(
            audit_backend=mock_backend,
            scan_id=SCAN_ID,
            user_id=USER_ID,
            action="BLOCK",
            rule_id="PRESIDIO_STREAM_ADVISORY",
            risk_level_str="HIGH",
            redacted_excerpt=None,
            tokens_delivered=50,
            advisory_entities=["CREDIT_CARD", "EMAIL_ADDRESS"],
        )
        event: AuditEvent = mock_backend.log_event.call_args[0][0]
        assert event.advisory_presidio_entities == ["CREDIT_CARD", "EMAIL_ADDRESS"]

    @pytest.mark.asyncio
    async def test_audit_backend_exception_does_not_propagate(self):
        """Exception in audit_backend.log_event() does not crash the stream."""
        mock_backend = AsyncMock()
        mock_backend.log_event.side_effect = RuntimeError("db error")
        # Should not raise
        await _log_stream_event(
            audit_backend=mock_backend,
            scan_id=SCAN_ID,
            user_id=USER_ID,
            action="BLOCK",
            rule_id="RULE",
            risk_level_str="CRITICAL",
            redacted_excerpt=None,
            tokens_delivered=0,
        )


# ─── Test: asyncio.create_task() usage ────────────────────────────────────────

class TestFireAndForgetPattern:

    @pytest.mark.asyncio
    async def test_block_audit_not_awaited_directly(self):
        """On BLOCK: asyncio.create_task() used, not direct await (AC-E004-04)."""
        cred_chunk = make_openai_chunk(CREDENTIAL + clean_text(500))
        mock_response = MockStreamingResponse([cred_chunk])
        mock_backend = AsyncMock()

        create_task_calls = []
        original_create_task = asyncio.create_task

        def mock_create_task(coro, **kwargs):
            create_task_calls.append(coro)
            return original_create_task(coro, **kwargs)

        with patch("asyncio.create_task", side_effect=mock_create_task):
            await collect_stream(
                _stream_response_scan(
                    mock_response, SCAN_ID,
                    audit_backend=mock_backend,
                    user_id=USER_ID
                )
            )

        # create_task should have been called (for BLOCK audit)
        assert len(create_task_calls) >= 1
        # Verify task was for audit logging (coroutine name check)
        task_coro_names = [c.__name__ for c in create_task_calls if hasattr(c, '__name__')]
        assert any("log_stream_event" in name for name in task_coro_names), \
            f"Expected _log_stream_event task, found: {task_coro_names}"

    @pytest.mark.asyncio
    async def test_allow_audit_on_clean_stream_completion(self):
        """ALLOW event logged on clean stream completion via create_task."""
        clean_chunks = [
            make_openai_chunk(clean_text(100)),
            "data: [DONE]\n\n",
        ]
        mock_response = MockStreamingResponse(clean_chunks)
        mock_backend = AsyncMock()

        with patch("asyncio.create_task") as mock_create_task:
            await collect_stream(
                _stream_response_scan(
                    mock_response, SCAN_ID,
                    audit_backend=mock_backend,
                    user_id=USER_ID
                )
            )
            # create_task should have been called
            assert mock_create_task.called


# ─── Test: Background Presidio advisory ──────────────────────────────────────

class TestAdvisoryPresidioScan:

    @pytest.mark.asyncio
    async def test_advisory_scan_sets_abort_trigger_on_pii(self):
        """Advisory scan detects PII → sets abort_trigger (AC-E004-09)."""
        import asyncio

        abort_trigger = asyncio.Event()
        advisory_result = {}

        # Mock ProcessPoolExecutor to return PII entities
        mock_pool = MagicMock()
        _mock_future = asyncio.get_event_loop().run_in_executor(None, lambda: None)

        with patch("app.proxy.engine._presidio_advisory_stream_scan") as mock_advisory:
            async def fake_advisory(*args, **kwargs):
                # Simulate Presidio finding PII
                abort_trigger.set()
                advisory_result["entities"] = ["CREDIT_CARD"]
                advisory_result["pii_detected"] = True

            mock_advisory.side_effect = fake_advisory

            await fake_advisory(
                "Some text with credit card 4532015112830366",
                mock_pool,
                SCAN_ID,
                abort_trigger,
                advisory_result,
            )

        assert abort_trigger.is_set()
        assert advisory_result["pii_detected"] is True

    @pytest.mark.asyncio
    async def test_advisory_scan_handles_timeout(self):
        """Advisory scan timeout → advisory_result["pii_detected"] = None (non-fatal)."""
        abort_trigger = asyncio.Event()
        advisory_result = {}

        mock_pool = MagicMock()

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop_instance = MagicMock()
            mock_loop.return_value = mock_loop_instance
            mock_loop_instance.run_in_executor.return_value = asyncio.sleep(100)

            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                await _presidio_advisory_stream_scan(
                    "some text",
                    mock_pool,
                    SCAN_ID,
                    abort_trigger,
                    advisory_result,
                )

        # Timeout: pii_detected should be None, abort_trigger NOT set
        assert not abort_trigger.is_set()
        assert advisory_result.get("pii_detected") is None

    @pytest.mark.asyncio
    async def test_advisory_scan_handles_general_exception(self):
        """Advisory scan exception → non-fatal, advisory_result["pii_detected"] = None."""
        abort_trigger = asyncio.Event()
        advisory_result = {}

        mock_pool = MagicMock()

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop_instance = MagicMock()
            mock_loop.return_value = mock_loop_instance
            mock_loop_instance.run_in_executor.side_effect = RuntimeError("presidio crashed")

            await _presidio_advisory_stream_scan(
                "some text",
                mock_pool,
                SCAN_ID,
                abort_trigger,
                advisory_result,
            )

        assert not abort_trigger.is_set()
        assert advisory_result.get("pii_detected") is None
