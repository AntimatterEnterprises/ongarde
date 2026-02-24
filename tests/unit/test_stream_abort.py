"""Unit tests for emit_stream_abort() — E-004-S-003.

Verifies:
  - Correct SSE abort sequence (AC-E004-03)
  - [DONE] emitted BEFORE ongarde_block
  - ongarde_block payload is valid JSON with required fields
  - tokens_delivered in payload matches passed value (AC-E004-04)
  - risk_level is always a valid string
  - Both chunks emitted within 100ms
  - suppression_hint and redacted_excerpt included when non-null
"""

from __future__ import annotations

import json
import time
from typing import List

import pytest

from app.models.scan import Action, RiskLevel, ScanResult
from app.proxy.streaming import StreamAbortPayload, emit_stream_abort

SCAN_ID = "01HXXXXXXXXXXXXXXXXXXXXXX_ABORT"


def make_block_result(
    rule_id: str = "CREDENTIAL_DETECTED",
    risk_level: RiskLevel = RiskLevel.CRITICAL,
    redacted_excerpt: str = None,
    suppression_hint: str = None,
) -> ScanResult:
    return ScanResult(
        action=Action.BLOCK,
        scan_id=SCAN_ID,
        rule_id=rule_id,
        risk_level=risk_level,
        redacted_excerpt=redacted_excerpt,
        suppression_hint=suppression_hint,
    )


async def collect_abort_chunks(scan_result: ScanResult, tokens_delivered: int) -> List[bytes]:
    """Helper: collect all chunks from emit_stream_abort() into a list."""
    chunks = []
    async for chunk in emit_stream_abort(scan_result, tokens_delivered=tokens_delivered):
        chunks.append(chunk)
    return chunks


# ─── Test: SSE Sequence (AC-E004-03) ─────────────────────────────────────────

class TestSSESequence:

    @pytest.mark.asyncio
    async def test_first_chunk_is_done(self):
        """AC-E004-03: [DONE] is emitted as the FIRST chunk."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=42)
        assert len(chunks) >= 1
        assert chunks[0] == b"data: [DONE]\n\n"

    @pytest.mark.asyncio
    async def test_second_chunk_is_ongarde_block(self):
        """AC-E004-03: ongarde_block event is emitted as the SECOND chunk."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=42)
        assert len(chunks) == 2
        second = chunks[1].decode("utf-8")
        assert second.startswith("event: ongarde_block\n")
        assert "data: " in second
        assert second.endswith("\n\n")

    @pytest.mark.asyncio
    async def test_exactly_two_chunks_emitted(self):
        """Exactly 2 chunks are yielded — no more, no less."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=0)
        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_done_before_block_event(self):
        """AC-E004-03: [DONE] appears strictly before ongarde_block."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=10)
        # Verify ordering — [DONE] is index 0, ongarde_block is index 1
        assert b"[DONE]" in chunks[0]
        assert b"ongarde_block" in chunks[1]

    @pytest.mark.asyncio
    async def test_both_chunks_within_100ms(self):
        """AC-E004-03: Both chunks emitted within 100ms of function call."""
        scan_result = make_block_result()
        t0 = time.perf_counter()
        chunks = await collect_abort_chunks(scan_result, tokens_delivered=100)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100.0, \
            f"emit_stream_abort() took {elapsed_ms:.1f}ms (must be < 100ms)"
        assert len(chunks) == 2


# ─── Test: Payload Content ────────────────────────────────────────────────────

class TestPayloadContent:

    @pytest.mark.asyncio
    async def test_payload_is_valid_json(self):
        """ongarde_block data field contains valid JSON."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=42)
        second = chunks[1].decode("utf-8")
        # Extract data line
        lines = second.strip().split("\n")
        data_line = next(ln for ln in lines if ln.startswith("data: "))
        json_str = data_line[len("data: "):]
        parsed = json.loads(json_str)  # Should not raise
        assert isinstance(parsed, dict)

    @pytest.mark.asyncio
    async def test_payload_has_required_fields(self):
        """Payload contains all required fields: scan_id, rule_id, risk_level, tokens_delivered, timestamp."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=42)
        second = chunks[1].decode("utf-8")
        lines = second.strip().split("\n")
        data_line = next(ln for ln in lines if ln.startswith("data: "))
        payload = json.loads(data_line[len("data: "):])

        assert "scan_id" in payload
        assert "rule_id" in payload
        assert "risk_level" in payload
        assert "tokens_delivered" in payload
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_payload_scan_id_matches(self):
        """scan_id in payload matches the scan_result.scan_id."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        assert payload["scan_id"] == SCAN_ID

    @pytest.mark.asyncio
    async def test_payload_rule_id_matches(self):
        """rule_id in payload matches scan_result.rule_id."""
        result = make_block_result(rule_id="CREDENTIAL_DETECTED")
        chunks = await collect_abort_chunks(result, tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        assert payload["rule_id"] == "CREDENTIAL_DETECTED"

    @pytest.mark.asyncio
    async def test_payload_rule_id_never_null(self):
        """AC-E004: rule_id defaults to 'SCANNER_ERROR' when scan_result.rule_id is None."""
        result = ScanResult(action=Action.BLOCK, scan_id=SCAN_ID, rule_id=None)
        chunks = await collect_abort_chunks(result, tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        assert payload["rule_id"] == "SCANNER_ERROR"
        assert payload["rule_id"] is not None

    @pytest.mark.asyncio
    async def test_payload_tokens_delivered_matches(self):
        """AC-E004-04: tokens_delivered in payload matches the passed value."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=256)
        payload = _parse_payload(chunks[1])
        assert payload["tokens_delivered"] == 256

    @pytest.mark.asyncio
    async def test_payload_tokens_delivered_zero(self):
        """tokens_delivered=0 is valid (block at first window)."""
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        assert payload["tokens_delivered"] == 0

    @pytest.mark.asyncio
    async def test_payload_risk_level_is_valid_string(self):
        """risk_level is always a valid string (CRITICAL/HIGH/MEDIUM/LOW)."""
        valid_levels = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        for level in [RiskLevel.CRITICAL, RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW]:
            result = make_block_result(risk_level=level)
            chunks = await collect_abort_chunks(result, tokens_delivered=0)
            payload = _parse_payload(chunks[1])
            assert payload["risk_level"] in valid_levels, \
                f"risk_level '{payload['risk_level']}' not in {valid_levels}"

    @pytest.mark.asyncio
    async def test_payload_risk_level_defaults_when_none(self):
        """risk_level defaults to 'CRITICAL' when scan_result.risk_level is None."""
        result = ScanResult(action=Action.BLOCK, scan_id=SCAN_ID, risk_level=None)
        chunks = await collect_abort_chunks(result, tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        assert payload["risk_level"] == "CRITICAL"

    @pytest.mark.asyncio
    async def test_payload_suppression_hint_included(self):
        """suppression_hint is included in payload when non-null."""
        hint = "# allowlist:\n  - rule_id: CREDENTIAL_DETECTED"
        result = make_block_result(suppression_hint=hint)
        chunks = await collect_abort_chunks(result, tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        assert "suppression_hint" in payload
        assert payload["suppression_hint"] == hint

    @pytest.mark.asyncio
    async def test_payload_redacted_excerpt_included(self):
        """redacted_excerpt is included in payload when non-null."""
        excerpt = "...found [REDACTED:openai-api-key] in text..."
        result = make_block_result(redacted_excerpt=excerpt)
        chunks = await collect_abort_chunks(result, tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        assert "redacted_excerpt" in payload
        assert payload["redacted_excerpt"] == excerpt

    @pytest.mark.asyncio
    async def test_payload_timestamp_is_iso8601_utc(self):
        """timestamp in payload is a valid ISO 8601 UTC string."""
        from datetime import datetime
        chunks = await collect_abort_chunks(make_block_result(), tokens_delivered=0)
        payload = _parse_payload(chunks[1])
        ts = payload["timestamp"]
        assert isinstance(ts, str)
        assert "T" in ts  # ISO 8601 separator
        # Should parse without error
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None


# ─── Test: StreamAbortPayload dataclass ──────────────────────────────────────

class TestStreamAbortPayload:

    def test_to_dict_excludes_none_fields(self):
        """to_dict() excludes None optional fields."""
        p = StreamAbortPayload(
            scan_id="test",
            rule_id="RULE",
            risk_level="HIGH",
            tokens_delivered=10,
            timestamp="2026-01-01T00:00:00+00:00",
        )
        d = p.to_dict()
        assert "scan_id" in d
        assert "redacted_excerpt" not in d
        assert "suppression_hint" not in d

    def test_to_dict_includes_optional_when_set(self):
        """to_dict() includes optional fields when non-None."""
        p = StreamAbortPayload(
            scan_id="test",
            rule_id="RULE",
            risk_level="HIGH",
            tokens_delivered=10,
            timestamp="2026-01-01T00:00:00+00:00",
            redacted_excerpt="...text...",
            suppression_hint="yaml here",
        )
        d = p.to_dict()
        assert d["redacted_excerpt"] == "...text..."
        assert d["suppression_hint"] == "yaml here"


# ─── Helper ───────────────────────────────────────────────────────────────────

def _parse_payload(chunk: bytes) -> dict:
    """Extract and parse the JSON payload from an ongarde_block SSE chunk."""
    text = chunk.decode("utf-8")
    lines = text.strip().split("\n")
    data_line = next(ln for ln in lines if ln.startswith("data: "))
    return json.loads(data_line[len("data: "):])
