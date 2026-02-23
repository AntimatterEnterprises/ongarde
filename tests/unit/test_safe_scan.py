"""Unit tests for app/scanner/safe_scan.py — E-001-S-006.

Tests the scan_or_block() stub:
  - Always returns Action.ALLOW
  - Returns a ScanResult with the correct scan_id
  - Never raises
  - Preserves the correct async signature for E-002 drop-in replacement

AC coverage:
  AC-E001-06 item 5: stub accepts content, returns ScanResult(action=ALLOW)
  DoD: app/scanner/safe_scan.py with scan_or_block() stub
"""

from __future__ import annotations

import asyncio

import pytest

from app.models.scan import Action, ScanResult
from app.scanner.safe_scan import scan_or_block


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _call_stub(
    content: str = "Hello, world!",
    scan_id: str = "01STUB000000000000000000000",
) -> ScanResult:
    return await scan_or_block(
        content=content,
        scan_pool=None,  # stub — no pool yet
        scan_id=scan_id,
        audit_context={"scan_id": scan_id},
    )


# ─── Stub behaviour ───────────────────────────────────────────────────────────


class TestScanOrBlockStub:
    """Tests for the scan_or_block() stub implementation."""

    @pytest.mark.asyncio
    async def test_returns_scan_result(self) -> None:
        """Stub returns a ScanResult instance."""
        result = await _call_stub()
        assert isinstance(result, ScanResult)

    @pytest.mark.asyncio
    async def test_always_returns_allow(self) -> None:
        """AC-E001-06 item 5: stub always returns Action.ALLOW."""
        result = await _call_stub()
        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_scan_id_preserved_in_result(self) -> None:
        """The scan_id argument is reflected in the returned ScanResult."""
        scan_id = "01CUSTOMSCANID0000000000000"
        result = await _call_stub(scan_id=scan_id)
        assert result.scan_id == scan_id

    @pytest.mark.asyncio
    async def test_never_returns_block(self) -> None:
        """Stub NEVER returns BLOCK regardless of content."""
        malicious_content = "sk-proj-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"
        result = await _call_stub(content=malicious_content)
        assert result.action == Action.ALLOW
        assert result.action != Action.BLOCK

    @pytest.mark.asyncio
    async def test_never_raises(self) -> None:
        """Stub does not raise for any input — preserves fail-safe invariant."""
        try:
            await _call_stub(content="")
            await _call_stub(content="a" * 10_000)
            await _call_stub(content="\x00\xff\x80")
        except Exception as exc:
            pytest.fail(f"scan_or_block() raised unexpectedly: {exc}")

    @pytest.mark.asyncio
    async def test_accepts_empty_content(self) -> None:
        """Empty content does not cause errors."""
        result = await _call_stub(content="")
        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_returns_allow_not_none(self) -> None:
        """Stub never returns None — always a ScanResult."""
        result = await _call_stub()
        assert result is not None

    @pytest.mark.asyncio
    async def test_is_coroutine(self) -> None:
        """scan_or_block() is a coroutine (async def), preserving E-002 signature."""
        import inspect
        assert inspect.iscoroutinefunction(scan_or_block)

    @pytest.mark.asyncio
    async def test_accepts_none_scan_pool(self) -> None:
        """scan_pool=None is valid (expected until E-003 is implemented)."""
        result = await scan_or_block(
            content="test",
            scan_pool=None,
            scan_id="s",
            audit_context={},
        )
        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_multiple_calls_all_allow(self) -> None:
        """Multiple concurrent calls all return ALLOW (no state mutation)."""
        results = await asyncio.gather(
            *[_call_stub(scan_id=f"01SCAN{i:020d}") for i in range(20)]
        )
        assert all(r.action == Action.ALLOW for r in results)
        assert all(isinstance(r, ScanResult) for r in results)
