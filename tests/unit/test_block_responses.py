"""Unit tests for app/models/block.py — E-001-S-006.

Tests the HTTP 400 BLOCK response builder and the HTTP 502 upstream-unavailable
response builder, verifying:
  - Correct HTTP status codes
  - Required headers (X-OnGarde-Block: true on BLOCK, absent on 502)
  - Required response body schemas
  - That the two failure modes are never confused

AC coverage:
  AC-E001-06 items 1–3: 502 body/headers, 400+X-OnGarde-Block body/headers
  AC-E001-06 item 8: BLOCK response body schema
  DoD: build_block_response() and build_upstream_unavailable_response()
"""

from __future__ import annotations

import json

import pytest

from app.models.block import build_block_response, build_upstream_unavailable_response
from app.models.scan import Action, RiskLevel, ScanResult


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _allow_result(scan_id: str = "01SCAN000000000000000000000") -> ScanResult:
    return ScanResult(action=Action.ALLOW, scan_id=scan_id)


def _block_result(
    scan_id: str = "01BLOCK00000000000000000000",
    rule_id: str | None = "CRED-001",
    risk_level: RiskLevel | None = RiskLevel.CRITICAL,
    redacted_excerpt: str | None = "sk-***REDACTED***",
) -> ScanResult:
    return ScanResult(
        action=Action.BLOCK,
        scan_id=scan_id,
        rule_id=rule_id,
        risk_level=risk_level,
        redacted_excerpt=redacted_excerpt,
        suppression_hint=None,
    )


# ─── build_block_response() ───────────────────────────────────────────────────


class TestBuildBlockResponse:
    """Tests for the HTTP 400 BLOCK response builder."""

    def test_returns_http_400(self) -> None:
        """BLOCK response must be HTTP 400."""
        response = build_block_response(_block_result())
        assert response.status_code == 400

    def test_has_x_ongarde_block_true(self) -> None:
        """AC-E001-06: X-OnGarde-Block: true is REQUIRED on ALL BLOCK responses."""
        response = build_block_response(_block_result())
        assert response.headers.get("X-OnGarde-Block") == "true"

    def test_has_x_ongarde_scan_id(self) -> None:
        """AC-E001-06: X-OnGarde-Scan-ID header matches the result scan_id."""
        scan_id = "01XSCANIDAAAAAAAAAAAAAAAAAA"
        response = build_block_response(_block_result(scan_id=scan_id))
        assert response.headers.get("X-OnGarde-Scan-ID") == scan_id

    def test_body_schema_error_field(self) -> None:
        """AC-E001-06 item 8: BLOCK body has OpenAI-compatible error field."""
        response = build_block_response(_block_result())
        body = json.loads(response.body)
        assert "error" in body
        assert body["error"]["message"] == "Request blocked by OnGarde security policy"
        assert body["error"]["type"] == "ongarde_block"
        assert body["error"]["code"] == "policy_violation"

    def test_body_schema_ongarde_field(self) -> None:
        """AC-E001-06 item 8: BLOCK body has OnGarde extension field."""
        scan_id = "01ONGARDE0000000000000000001"
        response = build_block_response(_block_result(scan_id=scan_id, rule_id="CRED-001"))
        body = json.loads(response.body)
        assert "ongarde" in body
        og = body["ongarde"]
        assert og["blocked"] is True
        assert og["rule_id"] == "CRED-001"
        assert og["scan_id"] == scan_id
        assert og["risk_level"] == "CRITICAL"
        assert og["suppression_hint"] is None  # AC-E001-06 item 9

    def test_suppression_hint_is_null(self) -> None:
        """AC-E001-06 item 9: suppression_hint present but null in this story."""
        response = build_block_response(_block_result())
        body = json.loads(response.body)
        assert body["ongarde"]["suppression_hint"] is None

    def test_no_raw_credentials_in_body(self) -> None:
        """No credentials or raw scan details leaked in the BLOCK response body.

        The error body must never contain internal scan data or raw credential text.
        redacted_excerpt is present but must be sanitised (***-masked) by callers.
        """
        response = build_block_response(_block_result(redacted_excerpt="sk-***REDACTED***"))
        body_str = response.body.decode()
        # Should not contain a fully-formed OpenAI API key pattern
        assert "sk-" not in body_str or "REDACTED" in body_str

    def test_block_with_null_rule_id(self) -> None:
        """rule_id can be null in the response (optional field)."""
        result = ScanResult(action=Action.BLOCK, scan_id="s")
        response = build_block_response(result)
        body = json.loads(response.body)
        assert body["ongarde"]["rule_id"] is None

    def test_block_with_null_risk_level(self) -> None:
        """risk_level serialises as null when not set."""
        result = ScanResult(action=Action.BLOCK, scan_id="s")
        response = build_block_response(result)
        body = json.loads(response.body)
        assert body["ongarde"]["risk_level"] is None

    def test_all_risk_levels_serialised(self) -> None:
        """Each RiskLevel value is correctly serialised to its string."""
        for level in RiskLevel:
            result = ScanResult(
                action=Action.BLOCK,
                scan_id="s",
                risk_level=level,
            )
            body = json.loads(build_block_response(result).body)
            assert body["ongarde"]["risk_level"] == level.value


# ─── build_upstream_unavailable_response() ───────────────────────────────────


class TestBuildUpstreamUnavailableResponse:
    """Tests for the HTTP 502 upstream-unavailable response builder."""

    def test_returns_http_502(self) -> None:
        """AC-E001-06 item 1: upstream unreachable → HTTP 502."""
        response = build_upstream_unavailable_response("01SCAN00000000000000000000")
        assert response.status_code == 502

    def test_no_x_ongarde_block_header(self) -> None:
        """AC-E001-06 item 1 / item 3: 502 must NOT include X-OnGarde-Block header.

        A connectivity failure is NOT a security block — these are never confused.
        """
        response = build_upstream_unavailable_response("01SCAN00000000000000000000")
        assert "X-OnGarde-Block" not in response.headers
        assert response.headers.get("X-OnGarde-Block") is None

    def test_has_x_ongarde_scan_id(self) -> None:
        """502 response carries X-OnGarde-Scan-ID for audit correlation."""
        scan_id = "01SCANID0000000000000000000"
        response = build_upstream_unavailable_response(scan_id)
        assert response.headers.get("X-OnGarde-Scan-ID") == scan_id

    def test_body_schema_error_field(self) -> None:
        """AC-E001-06 item 1: 502 body has correct error schema."""
        response = build_upstream_unavailable_response("s")
        body = json.loads(response.body)
        assert "error" in body
        assert body["error"]["message"] == "Upstream LLM provider unavailable"
        assert body["error"]["code"] == "upstream_unavailable"

    def test_body_includes_reason(self) -> None:
        """reason parameter is included in the detail field."""
        response = build_upstream_unavailable_response("s", reason="ConnectError")
        body = json.loads(response.body)
        assert body["error"]["detail"] == "ConnectError"

    def test_body_detail_null_when_no_reason(self) -> None:
        """detail is null when no reason provided."""
        response = build_upstream_unavailable_response("s")
        body = json.loads(response.body)
        assert body["error"]["detail"] is None

    def test_no_ongarde_block_field_in_body(self) -> None:
        """502 body must not contain 'blocked' or 'ongarde' fields."""
        response = build_upstream_unavailable_response("s")
        body = json.loads(response.body)
        assert "ongarde" not in body
        assert "blocked" not in body


# ─── Cross-type invariant: 502 ≠ 400 ────────────────────────────────────────


class TestFailureModeDistinction:
    """Verify that 502 and 400 BLOCK responses are never confused (AC-E001-06)."""

    def test_502_has_different_status_than_400(self) -> None:
        block_resp = build_block_response(_block_result())
        unavail_resp = build_upstream_unavailable_response("s")
        assert block_resp.status_code == 400
        assert unavail_resp.status_code == 502
        assert block_resp.status_code != unavail_resp.status_code

    def test_block_has_x_ongarde_block_502_does_not(self) -> None:
        """AC-E001-06 item 3: the critical distinction between the two modes."""
        block_resp = build_block_response(_block_result())
        unavail_resp = build_upstream_unavailable_response("s")
        # BLOCK has the header
        assert block_resp.headers.get("X-OnGarde-Block") == "true"
        # 502 does NOT have the header
        assert unavail_resp.headers.get("X-OnGarde-Block") is None

    def test_block_body_has_ongarde_field_502_does_not(self) -> None:
        """BLOCK body contains 'ongarde' extension; 502 body does not."""
        block_body = json.loads(build_block_response(_block_result()).body)
        unavail_body = json.loads(build_upstream_unavailable_response("s").body)
        assert "ongarde" in block_body
        assert "ongarde" not in unavail_body

    def test_block_error_code_policy_violation_502_upstream_unavailable(self) -> None:
        """Error codes are distinct between the two failure modes."""
        block_body = json.loads(build_block_response(_block_result()).body)
        unavail_body = json.loads(build_upstream_unavailable_response("s").body)
        assert block_body["error"]["code"] == "policy_violation"
        assert unavail_body["error"]["code"] == "upstream_unavailable"
