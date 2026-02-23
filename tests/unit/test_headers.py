"""Unit tests for E-001-S-003: Header forwarding, stripping, and ULID injection.

Covers ACs:
  AC-E001-03-1: Rate-limit headers from upstream present unchanged in agent response
  AC-E001-03-2: X-OnGarde-Key not present in upstream-bound request
  AC-E001-03-3: Authorization: Bearer ong-* stripped; Bearer sk-* forwarded unchanged
  AC-E001-03-4: Other headers forwarded unchanged (content-type, user-agent, anthropic-*, etc.)
  AC-E001-01-5: X-OnGarde-Scan-ID present with ULID value in every upstream request
"""

from __future__ import annotations

import re

import httpx
import pytest

from app.proxy.headers import (
    HOP_BY_HOP_HEADERS,
    build_agent_response_headers,
    build_upstream_headers,
)

# ─── ULID format ──────────────────────────────────────────────────────────────

ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
TEST_SCAN_ID = "01KJ0JRVHYA7KX32VPN5ZSCTMV"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _headers(*pairs: tuple[str, str]) -> list[tuple[str, str]]:
    """Build a list of header tuples for test input."""
    return list(pairs)


def _upstream_response_headers(**kwargs: str) -> httpx.Headers:
    """Build an httpx.Headers from keyword args (header-name=value)."""
    return httpx.Headers([(k.replace("_", "-"), v) for k, v in kwargs.items()])


# ─── build_upstream_headers() — scan ID injection ─────────────────────────────


class TestBuildUpstreamHeadersScanId:
    """X-OnGarde-Scan-ID injection tests."""

    def test_scan_id_injected(self) -> None:
        """X-OnGarde-Scan-ID must be present in the output headers."""
        result = build_upstream_headers(
            _headers(("Content-Type", "application/json")),
            scan_id=TEST_SCAN_ID,
        )
        assert "X-OnGarde-Scan-ID" in result

    def test_scan_id_value_matches(self) -> None:
        """X-OnGarde-Scan-ID value must equal the provided scan_id."""
        result = build_upstream_headers(
            _headers(("Content-Type", "application/json")),
            scan_id=TEST_SCAN_ID,
        )
        assert result["X-OnGarde-Scan-ID"] == TEST_SCAN_ID

    def test_scan_id_unique_per_call(self) -> None:
        """Different scan_ids produce different X-OnGarde-Scan-ID values."""
        scan_id_1 = "01KJ0JRVHYA7KX32VPN5ZSCTMV"
        scan_id_2 = "01KJ0JRVHYA7KX32VPN5ZSCTMW"
        result_1 = build_upstream_headers([], scan_id=scan_id_1)
        result_2 = build_upstream_headers([], scan_id=scan_id_2)
        assert result_1["X-OnGarde-Scan-ID"] != result_2["X-OnGarde-Scan-ID"]

    def test_scan_id_injected_even_with_empty_headers(self) -> None:
        """Scan ID is injected even when no request headers are forwarded."""
        result = build_upstream_headers([], scan_id=TEST_SCAN_ID)
        assert result == {"X-OnGarde-Scan-ID": TEST_SCAN_ID}


# ─── build_upstream_headers() — X-OnGarde-Key stripping ───────────────────────


class TestBuildUpstreamHeadersOnGardeKeyStrip:
    """X-OnGarde-Key must be stripped from upstream-bound requests (AC-E001-03-2)."""

    def test_x_ongarde_key_stripped(self) -> None:
        """X-OnGarde-Key must not appear in upstream headers."""
        result = build_upstream_headers(
            _headers(("X-OnGarde-Key", "ong-TESTKEY123")),
            scan_id=TEST_SCAN_ID,
        )
        assert "X-OnGarde-Key" not in result
        assert "x-ongarde-key" not in {k.lower() for k in result}

    def test_x_ongarde_key_case_insensitive(self) -> None:
        """Header name matching is case-insensitive."""
        result = build_upstream_headers(
            _headers(
                ("x-ongarde-key", "ong-TESTKEY123"),
                ("X-ONGARDE-KEY", "ong-ANOTHERKEY"),
            ),
            scan_id=TEST_SCAN_ID,
        )
        # x-ongarde-key (auth header) must be gone; X-OnGarde-Scan-ID (injected) is allowed
        assert "x-ongarde-key" not in {k.lower() for k in result}
        assert "X-ONGARDE-KEY" not in result

    def test_x_ongarde_key_stripped_other_headers_pass_through(self) -> None:
        """Other headers must still pass through after X-OnGarde-Key is stripped."""
        result = build_upstream_headers(
            _headers(
                ("X-OnGarde-Key", "ong-TESTKEY123"),
                ("Content-Type", "application/json"),
                ("User-Agent", "MyAgent/1.0"),
            ),
            scan_id=TEST_SCAN_ID,
        )
        assert "X-OnGarde-Key" not in result
        assert result.get("Content-Type") == "application/json"
        assert result.get("User-Agent") == "MyAgent/1.0"


# ─── build_upstream_headers() — Authorization header stripping ────────────────


class TestBuildUpstreamHeadersAuthStrip:
    """Authorization: Bearer ong-* stripped; other auth forwarded (AC-E001-03-3)."""

    def test_authorization_ong_bearer_stripped(self) -> None:
        """Authorization: Bearer ong-* must be stripped (OnGarde key in auth header)."""
        result = build_upstream_headers(
            _headers(("Authorization", "Bearer ong-mykey123")),
            scan_id=TEST_SCAN_ID,
        )
        assert "Authorization" not in result

    def test_authorization_ong_bearer_lowercase_header_stripped(self) -> None:
        """Case-insensitive header name matching for authorization."""
        result = build_upstream_headers(
            _headers(("authorization", "Bearer ong-mykey123")),
            scan_id=TEST_SCAN_ID,
        )
        assert "authorization" not in result
        assert "Authorization" not in result

    def test_authorization_sk_bearer_forwarded(self) -> None:
        """Authorization: Bearer sk-openai-* must be forwarded unchanged (LLM API key)."""
        result = build_upstream_headers(
            _headers(("Authorization", "Bearer sk-openai-ABCDEFGH")),
            scan_id=TEST_SCAN_ID,
        )
        assert result.get("Authorization") == "Bearer sk-openai-ABCDEFGH"

    def test_authorization_anthropic_key_forwarded(self) -> None:
        """Authorization with Anthropic API key prefix must be forwarded unchanged."""
        result = build_upstream_headers(
            _headers(("Authorization", "Bearer ant-abc123")),
            scan_id=TEST_SCAN_ID,
        )
        assert result.get("Authorization") == "Bearer ant-abc123"

    def test_authorization_generic_bearer_forwarded(self) -> None:
        """Any Bearer that does not start with 'ong-' is forwarded unchanged."""
        result = build_upstream_headers(
            _headers(("Authorization", "Bearer some-other-token")),
            scan_id=TEST_SCAN_ID,
        )
        assert result.get("Authorization") == "Bearer some-other-token"

    def test_authorization_basic_forwarded(self) -> None:
        """Authorization: Basic ... must be forwarded unchanged (not ong-specific)."""
        result = build_upstream_headers(
            _headers(("Authorization", "Basic dXNlcjpwYXNz")),
            scan_id=TEST_SCAN_ID,
        )
        assert result.get("Authorization") == "Basic dXNlcjpwYXNz"

    def test_ong_key_header_and_llm_auth_simultaneous(self) -> None:
        """X-OnGarde-Key stripped, LLM Authorization preserved simultaneously."""
        result = build_upstream_headers(
            _headers(
                ("X-OnGarde-Key", "ong-TESTKEY123"),
                ("Authorization", "Bearer sk-openai-LLMKEY"),
                ("Content-Type", "application/json"),
            ),
            scan_id=TEST_SCAN_ID,
        )
        # X-OnGarde-Key auth header stripped (X-OnGarde-Scan-ID is the injected tracer, allowed)
        assert "x-ongarde-key" not in {k.lower() for k in result}
        # LLM key preserved
        assert result.get("Authorization") == "Bearer sk-openai-LLMKEY"
        # Other headers preserved
        assert result.get("Content-Type") == "application/json"


# ─── build_upstream_headers() — hop-by-hop stripping ─────────────────────────


class TestBuildUpstreamHeadersHopByHop:
    """Hop-by-hop headers must be stripped from upstream-bound requests (RFC 7230 §6.1)."""

    @pytest.mark.parametrize(
        "header_name",
        [
            "connection",
            "Connection",
            "keep-alive",
            "Keep-Alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
            "host",
            "Host",
            "content-length",
            "Content-Length",
        ],
    )
    def test_hop_by_hop_headers_stripped(self, header_name: str) -> None:
        """Each hop-by-hop header must not appear in the upstream request."""
        result = build_upstream_headers(
            _headers((header_name, "some-value")),
            scan_id=TEST_SCAN_ID,
        )
        assert header_name.lower() not in {k.lower() for k in result}, (
            f"Hop-by-hop header {header_name!r} was not stripped"
        )

    def test_non_hop_by_hop_passes_through(self) -> None:
        """Regular headers (content-type, user-agent, etc.) must pass through."""
        headers_in = _headers(
            ("Content-Type", "application/json"),
            ("User-Agent", "MyAgent/1.0"),
            ("X-Request-ID", "req-12345"),
            ("anthropic-version", "2023-06-01"),
            ("openai-organization", "org-XYZ"),
        )
        result = build_upstream_headers(headers_in, scan_id=TEST_SCAN_ID)
        assert result.get("Content-Type") == "application/json"
        assert result.get("User-Agent") == "MyAgent/1.0"
        assert result.get("X-Request-ID") == "req-12345"
        assert result.get("anthropic-version") == "2023-06-01"
        assert result.get("openai-organization") == "org-XYZ"


# ─── build_upstream_headers() — comprehensive forwarding ─────────────────────


class TestBuildUpstreamHeadersForwarding:
    """All other headers must be forwarded unchanged (AC-E001-03-4)."""

    def test_content_type_forwarded(self) -> None:
        result = build_upstream_headers(
            _headers(("Content-Type", "application/json")),
            scan_id=TEST_SCAN_ID,
        )
        assert result.get("Content-Type") == "application/json"

    def test_user_agent_forwarded(self) -> None:
        result = build_upstream_headers(
            _headers(("User-Agent", "OpenClaw/1.0 (+https://openclaw.ai)")),
            scan_id=TEST_SCAN_ID,
        )
        assert result.get("User-Agent") == "OpenClaw/1.0 (+https://openclaw.ai)"

    def test_anthropic_headers_forwarded(self) -> None:
        """Anthropic-specific headers must be forwarded unchanged."""
        result = build_upstream_headers(
            _headers(
                ("anthropic-version", "2023-06-01"),
                ("anthropic-beta", "messages-2023-12-15"),
            ),
            scan_id=TEST_SCAN_ID,
        )
        assert result.get("anthropic-version") == "2023-06-01"
        assert result.get("anthropic-beta") == "messages-2023-12-15"

    def test_multiple_headers_forwarded(self) -> None:
        """All non-stripped headers must appear in the result."""
        headers_in = _headers(
            ("Content-Type", "application/json"),
            ("Authorization", "Bearer sk-openai-ABCD"),
            ("User-Agent", "Agent/1.0"),
            ("X-Custom-Header", "custom-value"),
            ("anthropic-version", "2023-06-01"),
        )
        result = build_upstream_headers(headers_in, scan_id=TEST_SCAN_ID)
        assert len([k for k in result if k != "X-OnGarde-Scan-ID"]) == 5

    def test_returns_dict(self) -> None:
        """Result must be a dict."""
        result = build_upstream_headers([], scan_id=TEST_SCAN_ID)
        assert isinstance(result, dict)


# ─── build_agent_response_headers() — rate-limit passthrough ──────────────────


class TestBuildAgentResponseHeadersRateLimits:
    """Rate-limit headers must be passed through unchanged (AC-E001-03-1)."""

    def test_ratelimit_limit_requests_forwarded(self) -> None:
        """x-ratelimit-limit-requests must be forwarded with exact value."""
        headers = httpx.Headers([("x-ratelimit-limit-requests", "1000")])
        result = build_agent_response_headers(headers)
        assert result.get("x-ratelimit-limit-requests") == "1000"

    def test_ratelimit_remaining_requests_forwarded(self) -> None:
        """x-ratelimit-remaining-requests must be forwarded with exact value."""
        headers = httpx.Headers([("x-ratelimit-remaining-requests", "999")])
        result = build_agent_response_headers(headers)
        assert result.get("x-ratelimit-remaining-requests") == "999"

    def test_ratelimit_limit_tokens_forwarded(self) -> None:
        """x-ratelimit-limit-tokens must be forwarded unchanged."""
        headers = httpx.Headers([("x-ratelimit-limit-tokens", "90000")])
        result = build_agent_response_headers(headers)
        assert result.get("x-ratelimit-limit-tokens") == "90000"

    def test_ratelimit_remaining_tokens_forwarded(self) -> None:
        """x-ratelimit-remaining-tokens must be forwarded unchanged."""
        headers = httpx.Headers([("x-ratelimit-remaining-tokens", "89500")])
        result = build_agent_response_headers(headers)
        assert result.get("x-ratelimit-remaining-tokens") == "89500"

    def test_retry_after_forwarded(self) -> None:
        """retry-after must be forwarded unchanged (AC-E001-03-1)."""
        headers = httpx.Headers([("retry-after", "30")])
        result = build_agent_response_headers(headers)
        assert result.get("retry-after") == "30"

    def test_retry_after_date_format_forwarded(self) -> None:
        """retry-after with HTTP date format must be forwarded exactly."""
        retry_val = "Fri, 21 Feb 2026 18:00:00 GMT"
        headers = httpx.Headers([("retry-after", retry_val)])
        result = build_agent_response_headers(headers)
        assert result.get("retry-after") == retry_val

    def test_all_ratelimit_headers_forwarded(self) -> None:
        """All rate-limit headers forwarded simultaneously."""
        headers = httpx.Headers([
            ("x-ratelimit-limit-requests", "1000"),
            ("x-ratelimit-remaining-requests", "999"),
            ("x-ratelimit-limit-tokens", "90000"),
            ("x-ratelimit-remaining-tokens", "89500"),
            ("retry-after", "60"),
            ("content-type", "application/json"),
        ])
        result = build_agent_response_headers(headers)
        assert result.get("x-ratelimit-limit-requests") == "1000"
        assert result.get("x-ratelimit-remaining-requests") == "999"
        assert result.get("x-ratelimit-limit-tokens") == "90000"
        assert result.get("x-ratelimit-remaining-tokens") == "89500"
        assert result.get("retry-after") == "60"
        assert result.get("content-type") == "application/json"


# ─── build_agent_response_headers() — hop-by-hop stripping ───────────────────


class TestBuildAgentResponseHeadersHopByHop:
    """Hop-by-hop headers must be stripped from upstream responses."""

    @pytest.mark.parametrize(
        "header_name",
        [
            "connection",
            "keep-alive",
            "transfer-encoding",
            "upgrade",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
        ],
    )
    def test_hop_by_hop_stripped_from_response(self, header_name: str) -> None:
        """Each hop-by-hop header must not appear in the agent-facing response."""
        headers = httpx.Headers([(header_name, "some-value")])
        result = build_agent_response_headers(headers)
        assert header_name.lower() not in {k.lower() for k in result}, (
            f"Hop-by-hop header {header_name!r} was not stripped from response"
        )

    def test_content_type_not_stripped_from_response(self) -> None:
        """content-type is not hop-by-hop and must pass through."""
        headers = httpx.Headers([("content-type", "application/json")])
        result = build_agent_response_headers(headers)
        assert result.get("content-type") == "application/json"

    def test_empty_upstream_response_headers(self) -> None:
        """Empty headers dict must produce empty result (just scan-id would not be here)."""
        headers = httpx.Headers([])
        result = build_agent_response_headers(headers)
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_returns_dict(self) -> None:
        """Result must be a dict."""
        headers = httpx.Headers([("content-type", "application/json")])
        result = build_agent_response_headers(headers)
        assert isinstance(result, dict)


# ─── HOP_BY_HOP_HEADERS constant ─────────────────────────────────────────────


class TestHopByHopConstant:
    """HOP_BY_HOP_HEADERS must be a frozenset and include required headers."""

    def test_is_frozenset(self) -> None:
        assert isinstance(HOP_BY_HOP_HEADERS, frozenset)

    @pytest.mark.parametrize(
        "header",
        [
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
            "host",
            "content-length",
        ],
    )
    def test_required_headers_present(self, header: str) -> None:
        """All RFC 7230 §6.1 hop-by-hop headers must be in the frozenset."""
        assert header in HOP_BY_HOP_HEADERS, (
            f"Required hop-by-hop header {header!r} not in HOP_BY_HOP_HEADERS"
        )

    def test_rate_limit_headers_not_in_hop_by_hop(self) -> None:
        """Rate-limit headers must NOT be in HOP_BY_HOP_HEADERS (they pass through)."""
        assert "x-ratelimit-limit-requests" not in HOP_BY_HOP_HEADERS
        assert "x-ratelimit-remaining-requests" not in HOP_BY_HOP_HEADERS
        assert "retry-after" not in HOP_BY_HOP_HEADERS

    def test_content_type_not_in_hop_by_hop(self) -> None:
        """content-type is an end-to-end header and must not be in HOP_BY_HOP_HEADERS."""
        assert "content-type" not in HOP_BY_HOP_HEADERS
