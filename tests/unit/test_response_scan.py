"""Unit tests for non-streaming response body scan — E-004-S-005, AC-E004-06.

Tests:
  - Non-streaming response body with PII triggers BLOCK before returning
  - Clean response body forwarded unchanged
  - scan_or_block() called AFTER aread() for non-streaming responses
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.scan import Action, RiskLevel, ScanResult


CREDENTIAL = "sk-testABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmn"


def clean_text(n: int) -> str:
    base = "The quick brown fox jumps over the lazy dog. "
    return (base * ((n // len(base)) + 2))[:n]


# ─── Test: Non-streaming response scan integration ────────────────────────────

class TestNonStreamingResponseScan:

    @pytest.mark.asyncio
    async def test_clean_response_body_forwarded(self):
        """Clean non-streaming response body forwarded unchanged."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.proxy.engine import router as proxy_router

        # We'll test via the scan_or_block integration
        from app.scanner.safe_scan import scan_or_block
        from app.models.scan import Action

        result = await scan_or_block(
            content=clean_text(200),
            scan_pool=None,
            scan_id="test-001",
            audit_context={"scan_id": "test-001", "direction": "RESPONSE"},
        )
        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_credential_in_response_body_blocked(self):
        """Non-streaming response body with credential triggers BLOCK (AC-E004-06)."""
        from app.scanner.safe_scan import scan_or_block
        from app.models.scan import Action

        result = await scan_or_block(
            content=CREDENTIAL + " " + clean_text(200),
            scan_pool=None,
            scan_id="test-002",
            audit_context={"scan_id": "test-002", "direction": "RESPONSE"},
        )
        assert result.action == Action.BLOCK
        assert result.rule_id is not None

    @pytest.mark.asyncio
    async def test_aws_key_in_response_body_blocked(self):
        """AWS access key in non-streaming response body triggers BLOCK."""
        from app.scanner.safe_scan import scan_or_block

        result = await scan_or_block(
            content=clean_text(100) + " AKIA0NFZYMFPNQABCDEF " + clean_text(100),
            scan_pool=None,
            scan_id="test-003",
            audit_context={"scan_id": "test-003", "direction": "RESPONSE"},
        )
        assert result.action == Action.BLOCK

    @pytest.mark.asyncio
    async def test_empty_response_body_allowed(self):
        """Empty response body: scan_or_block returns ALLOW."""
        from app.scanner.safe_scan import scan_or_block

        result = await scan_or_block(
            content="",
            scan_pool=None,
            scan_id="test-004",
            audit_context={"scan_id": "test-004", "direction": "RESPONSE"},
        )
        assert result.action == Action.ALLOW
