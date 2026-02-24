"""Unit tests for _stream_response_scan() and SSE content extraction — E-004-S-002.

Tests:
  - _extract_content_from_sse_message() for OpenAI and Anthropic formats
  - _stream_response_scan() with clean and blocked streams
  - Abort sequence emitted on BLOCK (AC-E004-01)
  - Presidio NOT called sync during streaming
  - aiter_bytes() used for streaming (not aread() — AC-E004-07)
"""

from __future__ import annotations

import json
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.proxy.engine import _extract_content_from_sse_message, _stream_response_scan

# ─── Fixtures / Helpers ───────────────────────────────────────────────────────

SCAN_ID = "01HXXXXXXXXXX_STREAM_TEST"
CREDENTIAL = "sk-testABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmn"


def make_openai_chunk(content: str) -> str:
    """Build an OpenAI-format SSE message with the given content."""
    data = {
        "object": "chat.completion.chunk",
        "choices": [{"delta": {"content": content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(data)}\n\n"


def make_anthropic_chunk(text: str) -> str:
    """Build an Anthropic-format content_block_delta SSE message."""
    data = {
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": text},
    }
    return f"event: content_block_delta\ndata: {json.dumps(data)}\n\n"


def make_done_chunk() -> str:
    return "data: [DONE]\n\n"


def clean_text(n: int) -> str:
    base = "The quick brown fox jumps over the lazy dog. "
    return (base * ((n // len(base)) + 2))[:n]


class MockStreamingResponse:
    """Simulates an httpx streaming response for testing."""

    def __init__(self, chunks: list[str], status_code: int = 200):
        self._chunks = [c.encode("utf-8") for c in chunks]
        self.status_code = status_code
        self._closed = False

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        raise AssertionError("aread() must NOT be called during streaming — use aiter_bytes()")

    async def aclose(self) -> None:
        self._closed = True


async def collect_stream(generator) -> list[bytes]:
    """Collect all bytes from an async generator."""
    chunks = []
    async for chunk in generator:
        chunks.append(chunk)
    return chunks


# ─── Test: _extract_content_from_sse_message ────────────────────────────────

class TestExtractSseContent:

    def test_openai_delta_content_extracted(self):
        """OpenAI format: choices[0].delta.content extracted correctly."""
        msg = 'data: {"choices": [{"delta": {"content": "hello world"}}]}'
        assert _extract_content_from_sse_message(msg) == "hello world"

    def test_anthropic_text_delta_extracted(self):
        """Anthropic format: content_block_delta.delta.text extracted correctly."""
        msg = 'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi there"}}'
        assert _extract_content_from_sse_message(msg) == "hi there"

    def test_done_message_returns_empty(self):
        """data: [DONE] returns empty string (stream terminator)."""
        assert _extract_content_from_sse_message("data: [DONE]") == ""

    def test_malformed_json_returns_empty(self):
        """Malformed JSON returns empty string without crashing."""
        assert _extract_content_from_sse_message("data: not-valid-json{") == ""

    def test_empty_content_field_returns_empty(self):
        """Empty delta.content returns empty string."""
        msg = 'data: {"choices": [{"delta": {"content": ""}}]}'
        assert _extract_content_from_sse_message(msg) == ""

    def test_missing_content_field_returns_empty(self):
        """Missing delta.content returns empty string (role-only delta)."""
        msg = 'data: {"choices": [{"delta": {"role": "assistant"}}]}'
        assert _extract_content_from_sse_message(msg) == ""

    def test_no_data_line_returns_empty(self):
        """SSE message with no data: line returns empty string."""
        msg = "event: ping"
        assert _extract_content_from_sse_message(msg) == ""

    def test_multiline_openai_chunk(self):
        """OpenAI chunk with event: line before data: line."""
        msg = 'event: chat.completion\ndata: {"choices": [{"delta": {"content": "test"}}]}'
        assert _extract_content_from_sse_message(msg) == "test"

    def test_anthropic_non_text_delta_returns_empty(self):
        """Anthropic input_json_delta (non-text) returns empty string."""
        msg = 'data: {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{}"}}'
        assert _extract_content_from_sse_message(msg) == ""

    def test_anthropic_message_stop_returns_empty(self):
        """Anthropic message_stop event returns empty string."""
        msg = 'data: {"type": "message_stop"}'
        assert _extract_content_from_sse_message(msg) == ""


# ─── Test: _stream_response_scan — clean streams ─────────────────────────────

class TestStreamResponseScanClean:

    @pytest.mark.asyncio
    async def test_clean_stream_all_chunks_forwarded(self):
        """Clean stream (no threats): all SSE chunks forwarded to agent."""
        chunks = [
            make_openai_chunk(clean_text(100)),
            make_openai_chunk(clean_text(100)),
            make_done_chunk(),
        ]
        mock_response = MockStreamingResponse(chunks)

        result_bytes = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID)
        )

        assert len(result_bytes) > 0
        combined = b"".join(result_bytes).decode("utf-8")
        assert "DONE" in combined

    @pytest.mark.asyncio
    async def test_clean_stream_never_calls_aread(self):
        """Streaming path uses aiter_bytes() — aread() must not be called (AC-E004-07)."""
        chunks = [make_openai_chunk("hello"), make_done_chunk()]
        mock_response = MockStreamingResponse(chunks)

        # If aread() is called, MockStreamingResponse.aread() raises AssertionError
        result_bytes = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID)
        )
        # No AssertionError means aread() was not called
        assert len(result_bytes) > 0

    @pytest.mark.asyncio
    async def test_empty_stream_no_crash(self):
        """Empty stream (only [DONE]): no crash, returns empty/minimal bytes."""
        chunks = [make_done_chunk()]
        mock_response = MockStreamingResponse(chunks)

        result_bytes = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID)
        )
        # Should complete without error
        assert result_bytes is not None

    @pytest.mark.asyncio
    async def test_anthropic_format_correctly_forwarded(self):
        """Anthropic-format SSE stream is parsed and forwarded correctly."""
        chunks = [
            make_anthropic_chunk("Hello, "),
            make_anthropic_chunk("world!"),
            "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n",
        ]
        mock_response = MockStreamingResponse(chunks)

        result_bytes = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID)
        )

        combined = b"".join(result_bytes).decode("utf-8")
        assert "Hello" in combined or "world" in combined

    @pytest.mark.asyncio
    async def test_audit_log_task_created_on_clean_stream(self):
        """asyncio.create_task() called for ALLOW audit event on clean stream."""
        chunks = [make_openai_chunk(clean_text(50)), make_done_chunk()]
        mock_response = MockStreamingResponse(chunks)
        mock_backend = AsyncMock()

        with patch("asyncio.create_task") as mock_create_task:
            _result_bytes = await collect_stream(
                _stream_response_scan(
                    mock_response, SCAN_ID,
                    audit_backend=mock_backend,
                    user_id="user-123"
                )
            )
            # create_task should have been called (for ALLOW audit)
            assert mock_create_task.called


# ─── Test: _stream_response_scan — BLOCK scenarios ───────────────────────────

class TestStreamResponseScanBlock:

    @pytest.mark.asyncio
    async def test_credential_in_stream_triggers_abort(self):
        """A credential in the stream triggers abort sequence (AC-E004-01)."""
        # Build a stream with a credential embedded after some clean content
        cred_content = clean_text(400) + CREDENTIAL + clean_text(200)
        chunks = [
            make_openai_chunk(cred_content),
        ]
        mock_response = MockStreamingResponse(chunks)

        result_bytes = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID)
        )

        combined = b"".join(result_bytes).decode("utf-8")
        # Must contain the abort markers
        assert "[DONE]" in combined
        assert "ongarde_block" in combined

    @pytest.mark.asyncio
    async def test_block_emits_done_before_ongarde_block(self):
        """On BLOCK: [DONE] is emitted before ongarde_block (AC-E004-03)."""
        cred_chunk = make_openai_chunk(CREDENTIAL + clean_text(500))
        mock_response = MockStreamingResponse([cred_chunk])

        result_bytes = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID)
        )

        combined = b"".join(result_bytes).decode("utf-8")
        done_pos = combined.find("[DONE]")
        block_pos = combined.find("ongarde_block")

        assert done_pos != -1, "[DONE] not found in output"
        assert block_pos != -1, "ongarde_block not found in output"
        assert done_pos < block_pos, "[DONE] must appear before ongarde_block"

    @pytest.mark.asyncio
    async def test_block_audit_event_created_via_create_task(self):
        """On BLOCK: audit event created via asyncio.create_task() (not awaited)."""
        cred_chunk = make_openai_chunk(CREDENTIAL + clean_text(500))
        mock_response = MockStreamingResponse([cred_chunk])
        mock_backend = AsyncMock()

        with patch("asyncio.create_task") as mock_create_task:
            _result_bytes = await collect_stream(
                _stream_response_scan(
                    mock_response, SCAN_ID,
                    audit_backend=mock_backend,
                    user_id="user-abc"
                )
            )
            # create_task called for the BLOCK audit event
            assert mock_create_task.called

    @pytest.mark.asyncio
    async def test_no_audit_backend_no_crash(self):
        """audit_backend=None: no crash during BLOCK (graceful no-op)."""
        cred_chunk = make_openai_chunk(CREDENTIAL + clean_text(500))
        mock_response = MockStreamingResponse([cred_chunk])

        # Should not raise
        result_bytes = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID, audit_backend=None)
        )
        assert len(result_bytes) > 0

    @pytest.mark.asyncio
    async def test_presidio_not_called_sync_in_streaming(self):
        """Presidio is NOT called synchronously during window scans (regex only)."""
        from app.scanner import presidio_worker as pw_module

        clean_chunks = [
            make_openai_chunk(clean_text(200)),
            make_openai_chunk(clean_text(200)),
            make_openai_chunk(clean_text(200)),
            make_done_chunk(),
        ]
        mock_response = MockStreamingResponse(clean_chunks)

        with patch.object(pw_module, "presidio_scan_worker") as mock_presidio:
            _result_bytes = await collect_stream(
                _stream_response_scan(mock_response, SCAN_ID, scan_pool=None)
            )
            # With scan_pool=None, Presidio advisory is skipped entirely
            mock_presidio.assert_not_called()


# ─── Test: Streaming tracker integration ─────────────────────────────────────

class TestStreamingTrackerIntegration:

    @pytest.mark.asyncio
    async def test_streaming_tracker_opened_and_closed(self):
        """stream_opened() and stream_closed() called via tracker."""
        chunks = [make_openai_chunk("hello"), make_done_chunk()]
        mock_response = MockStreamingResponse(chunks)

        mock_tracker = MagicMock()

        await collect_stream(
            _stream_response_scan(
                mock_response, SCAN_ID,
                streaming_tracker=mock_tracker
            )
        )

        mock_tracker.stream_opened.assert_called_once()
        mock_tracker.stream_closed.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_tracker_closed_even_on_block(self):
        """stream_closed() called even when stream is aborted by BLOCK."""
        cred_chunk = make_openai_chunk(CREDENTIAL + clean_text(500))
        mock_response = MockStreamingResponse([cred_chunk])
        mock_tracker = MagicMock()

        await collect_stream(
            _stream_response_scan(
                mock_response, SCAN_ID,
                streaming_tracker=mock_tracker
            )
        )

        mock_tracker.stream_opened.assert_called_once()
        mock_tracker.stream_closed.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_tracker_no_crash(self):
        """streaming_tracker=None: no crash."""
        chunks = [make_openai_chunk("hello"), make_done_chunk()]
        mock_response = MockStreamingResponse(chunks)

        result = await collect_stream(
            _stream_response_scan(mock_response, SCAN_ID, streaming_tracker=None)
        )
        assert result is not None
