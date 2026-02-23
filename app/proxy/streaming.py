"""SSE streaming abort mechanism for OnGarde — E-004-S-003.

Provides ``emit_stream_abort()``: the SSE abort sequence generator that is yielded
when a streaming window scan detects a threat.

The abort sequence consists of exactly two SSE events:
  1. ``data: [DONE]\\n\\n``           — closes the SSE stream for standard clients
  2. ``event: ongarde_block\\n``
     ``data: {payload_json}\\n\\n``   — signals the block reason to OnGarde-aware clients

AC-E004-03: Both events are emitted within 100ms of threat detection (pure in-memory
operation — no I/O, no network, no database calls).
AC-E004-04: ``tokens_delivered`` in the payload reflects byte-based approximation
from StreamingScanner.tokens_delivered (±20% of actual token count).

**Prior version removed:**
The original ``app/proxy/streaming.py`` contained skeleton SSE parsing code
(StreamChunk, parse_sse_stream, stream_openai_response, stream_anthropic_response,
buffer_stream_for_inspection, inspect_stream_content). These were pre-BMad placeholders
that were never used in the production proxy path (engine.py uses aiter_bytes() directly).
They are removed here. SSE content extraction is now in engine.py
(_extract_content_from_sse_message() — E-004-S-002).

Architecture references: architecture.md §2.2, §8
Stories: E-004-S-003 (this module), E-004-S-002 (caller in engine.py)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from app.models.scan import ScanResult

logger = logging.getLogger(__name__)


# ─── Payload ──────────────────────────────────────────────────────────────────


@dataclass
class StreamAbortPayload:
    """Typed representation of the ongarde_block SSE event payload.

    Serialised to JSON and included in the ``event: ongarde_block`` SSE event.

    Fields:
        scan_id:           ULID from the parent scan (correlates with audit event).
        rule_id:           Rule that triggered the block (e.g. "CREDENTIAL_DETECTED").
        risk_level:        String representation of RiskLevel ("CRITICAL", etc.)
        tokens_delivered:  Byte-approx token count forwarded before abort (±20%).
        timestamp:         ISO 8601 UTC string when abort was triggered.
        redacted_excerpt:  Sanitised context excerpt (never contains raw PII).
        suppression_hint:  YAML allowlist snippet to suppress this rule (if policy block).
    """

    scan_id: str
    rule_id: str
    risk_level: str
    tokens_delivered: int
    timestamp: str
    redacted_excerpt: Optional[str] = None
    suppression_hint: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to JSON-serialisable dict (None fields excluded)."""
        d = {
            "scan_id": self.scan_id,
            "rule_id": self.rule_id,
            "risk_level": self.risk_level,
            "tokens_delivered": self.tokens_delivered,
            "timestamp": self.timestamp,
        }
        if self.redacted_excerpt is not None:
            d["redacted_excerpt"] = self.redacted_excerpt
        if self.suppression_hint is not None:
            d["suppression_hint"] = self.suppression_hint
        return d


# ─── Abort Sequence Generator ─────────────────────────────────────────────────


async def emit_stream_abort(
    scan_result: ScanResult,
    tokens_delivered: int,
) -> AsyncGenerator[bytes, None]:
    """Yield the SSE abort sequence for a streaming BLOCK event.

    MUST be called inside an async generator (used via ``async for`` in engine.py).
    Yields exactly two chunks of bytes — purely in-memory, no I/O.

    Sequence (AC-E004-03):
      1. b"data: [DONE]\\n\\n"
         Closes the stream for standard SSE clients (prevents hanging).

      2. b"event: ongarde_block\\ndata: {payload}\\n\\n"
         Signals the block to OnGarde-aware clients.
         Clients that don't understand ``ongarde_block`` silently ignore it
         (SSE spec §8.9 — unknown event types are discarded).

    AC-E004-03: Both chunks are in-memory byte constants — total emission < 1ms.
    AC-E004-04: ``tokens_delivered`` in payload comes from StreamingScanner.tokens_delivered.

    Args:
        scan_result:      ScanResult(action=BLOCK) from StreamingScanner.
        tokens_delivered: From scanner.tokens_delivered at time of abort.

    Yields:
        bytes: First the [DONE] chunk, then the ongarde_block chunk.
    """
    now_utc = datetime.now(timezone.utc).isoformat()

    payload = StreamAbortPayload(
        scan_id=scan_result.scan_id,
        rule_id=scan_result.rule_id or "SCANNER_ERROR",
        risk_level=(
            scan_result.risk_level.value
            if scan_result.risk_level is not None
            else "CRITICAL"
        ),
        tokens_delivered=tokens_delivered,
        timestamp=now_utc,
        redacted_excerpt=scan_result.redacted_excerpt,
        suppression_hint=scan_result.suppression_hint,
    )

    # Step 1: Emit [DONE] — closes SSE stream for standard clients
    yield b"data: [DONE]\n\n"

    # Step 2: Emit ongarde_block event — signals block reason to aware clients
    payload_json = json.dumps(payload.to_dict(), ensure_ascii=False)
    sse_event = f"event: ongarde_block\ndata: {payload_json}\n\n"
    yield sse_event.encode("utf-8")

    logger.info(
        "stream_aborted",
        scan_id=scan_result.scan_id,
        rule_id=payload.rule_id,
        risk_level=payload.risk_level,
        tokens_delivered=tokens_delivered,
    )
