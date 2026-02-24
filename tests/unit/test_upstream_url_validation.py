"""Tests for upstream URL SSRF validation in config.py — SEC-003.

Verifies _validate_upstream_url() rejects dangerous URLs and accepts
valid ones: https:// external providers and http://localhost:* for
local models (Ollama).
"""

from __future__ import annotations

import pytest

from app.config import _validate_upstream_url


class TestValidUpstreamURLs:
    """URLs that must be accepted without error."""

    def test_https_openai(self) -> None:
        _validate_upstream_url("https://api.openai.com", "upstream.openai")

    def test_https_anthropic(self) -> None:
        _validate_upstream_url("https://api.anthropic.com", "upstream.anthropic")

    def test_https_custom_domain(self) -> None:
        _validate_upstream_url("https://my-llm-proxy.example.com", "upstream.custom")

    def test_http_localhost_ollama(self) -> None:
        _validate_upstream_url("http://localhost:11434", "upstream.custom")

    def test_http_127_0_0_1(self) -> None:
        _validate_upstream_url("http://127.0.0.1:11434", "upstream.custom")

    def test_https_with_port(self) -> None:
        _validate_upstream_url("https://api.openai.com:443", "upstream.openai")

    def test_https_with_path(self) -> None:
        _validate_upstream_url("https://api.openai.com/v1", "upstream.openai")

    def test_http_docker_service_name(self) -> None:
        """Docker/K8s service names via http:// are allowed (domain, not raw IP)."""
        _validate_upstream_url("http://my-openai-proxy:8080", "upstream.openai")

    def test_http_docker_ollama_service(self) -> None:
        _validate_upstream_url("http://ollama:11434", "upstream.custom")


class TestBlockedUpstreamURLs:
    """URLs that must be rejected with SystemExit."""

    def test_metadata_service_ipv4(self) -> None:
        """AWS/GCP/Azure metadata service IP blocked."""
        with pytest.raises(SystemExit):
            _validate_upstream_url("http://169.254.169.254", "upstream.openai")

    def test_rfc1918_10_block(self) -> None:
        """Direct RFC 1918 IP address blocked."""
        with pytest.raises(SystemExit):
            _validate_upstream_url("http://10.0.0.1:8080", "upstream.openai")

    def test_rfc1918_192_168(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("http://192.168.1.100", "upstream.openai")

    def test_rfc1918_172_16(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("http://172.16.0.1", "upstream.openai")

    def test_carrier_grade_nat(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("http://100.64.0.1", "upstream.openai")

    def test_embedded_credentials(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("https://user:pass@api.openai.com", "upstream.openai")

    def test_ftp_scheme(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("ftp://api.openai.com", "upstream.openai")

    def test_file_scheme(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("file:///etc/passwd", "upstream.openai")

    def test_empty_string(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("", "upstream.openai")

    def test_no_hostname(self) -> None:
        with pytest.raises(SystemExit):
            _validate_upstream_url("https://", "upstream.openai")
