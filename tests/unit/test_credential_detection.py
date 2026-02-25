# CI GATE: This test suite is a hard pass/fail gate.
# Any failure means a credential pattern is broken or missing.
# Do NOT add pytest.mark.skip or xfail to any test in this file.
# Block rate must be 100% — see AC-E002-01.
"""Credential detection test suite — E-002-S-003.

Verifies 100% block rate across ≥ 20 distinct credential patterns from every
supported provider. This file is a CI gate: any failure blocks merging.

AC coverage: AC-E002-01, AC-S003-01 through AC-S003-06
"""

from __future__ import annotations

import pytest

from app.models.scan import RiskLevel
from app.scanner.regex_engine import regex_scan

# ---------------------------------------------------------------------------
# Positive test cases — MUST all produce is_block=True, risk_level=CRITICAL
# ---------------------------------------------------------------------------

CREDENTIAL_POSITIVES = [
    # (test_id, input_text, description)

    # ── OpenAI ───────────────────────────────────────────────────────────
    (
        "openai_classic",
        "sk-" + "a" * 20 + "T3BlbkFJ" + "b" * 20,
        "OpenAI classic API key format",
    ),
    (
        "openai_classic_embedded",
        "Please use key sk-" + "a" * 20 + "T3BlbkFJ" + "b" * 20 + " for this call",
        "OpenAI classic key embedded in sentence",
    ),
    (
        "openai_project",
        "sk-proj-" + "a" * 50,
        "OpenAI project key sk-proj- prefix",
    ),
    (
        "openai_project_embedded",
        "API key: sk-proj-" + "z" * 52,
        "OpenAI project key embedded in config snippet",
    ),
    (
        "openai_generic_48",
        "sk-" + "a" * 48,
        "OpenAI generic 48-char key",
    ),

    # ── Anthropic ────────────────────────────────────────────────────────
    (
        "anthropic_key",
        "sk-ant-api03-" + "A" * 93,
        "Anthropic API key full format",
    ),
    (
        "anthropic_embedded",
        "My anthropic key is: sk-ant-api03-" + "B" * 93,
        "Anthropic key embedded in request body",
    ),

    # ── AWS ──────────────────────────────────────────────────────────────
    (
        "aws_akia",
        "AKIA" + "IOSFODNN7EXAMPLE",  # Split to avoid secret scanning false positive
        "AWS AKIA Access Key ID",
    ),
    (
        "aws_asia",
        "ASIA" + "IOSFODNN7EXAMPLE",  # Split to avoid secret scanning false positive
        "AWS ASIA temporary STS credential",
    ),
    (
        "aws_secret",
        "aws_secret_access_key: " + "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "AWS Secret Access Key with context keyword",
    ),
    (
        "aws_secret_embedded",
        "export AWS_SECRET_ACCESS_KEY=" + "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "AWS secret key as shell export",
    ),

    # ── GitHub ───────────────────────────────────────────────────────────
    (
        "github_pat_ghp",
        "ghp_" + "a" * 36,
        "GitHub personal access token ghp_ prefix",
    ),
    (
        "github_pat_gho",
        "gho_" + "b" * 36,
        "GitHub OAuth token gho_ prefix",
    ),
    (
        "github_fine_grained",
        "github_pat_" + "a" * 22 + "_" + "b" * 59,
        "GitHub fine-grained PAT",
    ),
    (
        "github_ghp_embedded",
        "GITHUB_TOKEN=" + "ghp_" + "c" * 36,
        "GitHub PAT embedded in env assignment",
    ),

    # ── Bearer token ─────────────────────────────────────────────────────
    (
        "bearer_token",
        "Authorization: Bearer " + "x" * 64,
        "Generic Bearer token (64 chars)",
    ),
    (
        "bearer_token_long",
        "Authorization: Bearer " + "y" * 128,
        "Generic Bearer token (128 chars)",
    ),
    (
        "bearer_token_embedded",
        'headers = {"Authorization": "Bearer ' + "z" * 80 + '"}',
        "Bearer token embedded in Python dict",
    ),

    # ── Stripe ───────────────────────────────────────────────────────────
    (
        "stripe_live",
        "sk_live_" + "a" * 24,
        "Stripe live secret key",
    ),
    (
        "stripe_restricted",
        "rk_live_" + "b" * 24,
        "Stripe restricted key",
    ),
    (
        "stripe_live_embedded",
        "STRIPE_SECRET_KEY=sk_live_" + "c" * 24,
        "Stripe live key in env var",
    ),

    # ── HuggingFace ──────────────────────────────────────────────────────
    (
        "huggingface",
        "hf_" + "a" * 34,
        "HuggingFace API token (34 chars)",
    ),
    (
        "huggingface_long",
        "hf_" + "b" * 40,
        "HuggingFace API token (40 chars)",
    ),

    # ── Slack ────────────────────────────────────────────────────────────
    (
        "slack_bot",
        "xoxb-1234567890-1234567890123-" + "a" * 24,
        "Slack bot token xoxb- format",
    ),
    (
        "slack_bot_embedded",
        "SLACK_TOKEN=xoxb-9876543210-9876543210987-" + "s" * 24,
        "Slack bot token in env var",
    ),

    # ── Twilio ───────────────────────────────────────────────────────────
    (
        "twilio_sid",
        "AC" + "a" * 32,
        "Twilio Account SID",
    ),
    (
        "twilio_sid_hex",
        "AC" + "0" * 32,
        "Twilio Account SID all-zero hex",
    ),

    # ── Google ───────────────────────────────────────────────────────────
    (
        "google_api_key",
        "AIza" + "a" * 35,
        "Google API key AIza prefix",
    ),
    (
        "google_api_embedded",
        "GOOGLE_API_KEY=AIza" + "b" * 35,
        "Google API key in env assignment",
    ),

    # ── SendGrid ─────────────────────────────────────────────────────────
    (
        "sendgrid",
        "SG." + "a" * 22 + "." + "b" * 43,
        "SendGrid API key SG. format",
    ),
    (
        "sendgrid_embedded",
        "SENDGRID_API_KEY=SG." + "x" * 22 + "." + "y" * 43,
        "SendGrid key in env var",
    ),

    # ── NPM ──────────────────────────────────────────────────────────────
    (
        "npm_token",
        "npm_" + "a" * 36,
        "NPM access token npm_ prefix",
    ),

    # ── PyPI ─────────────────────────────────────────────────────────────
    (
        "pypi_token",
        "pypi-" + "a" * 50,
        "PyPI API token pypi- prefix",
    ),

    # ── PEM private keys ─────────────────────────────────────────────────
    (
        "rsa_private_key",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK...",
        "RSA private key PEM block",
    ),
    (
        "openssh_private_key",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1...",
        "OpenSSH private key PEM block",
    ),
    (
        "ec_private_key",
        "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEIA...",
        "EC private key PEM block",
    ),
    (
        "generic_private_key",
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC...",
        "Generic PKCS8 private key PEM block",
    ),
]


@pytest.mark.parametrize(
    "test_id,text,description",
    CREDENTIAL_POSITIVES,
    ids=[x[0] for x in CREDENTIAL_POSITIVES],
)
def test_credential_blocked(test_id: str, text: str, description: str) -> None:
    """All credential patterns must produce is_block=True. Block rate = 100%."""
    result = regex_scan(text)
    assert result.is_block is True, (
        f"CREDENTIAL NOT BLOCKED [{test_id}]: {description!r}\n"
        f"Input (first 100 chars): {text[:100]!r}\n"
        f"Expected: is_block=True, Got: is_block=False"
    )
    assert result.risk_level == RiskLevel.CRITICAL, (
        f"Credential [{test_id}] should have CRITICAL risk, got {result.risk_level}"
    )
    assert result.rule_id == "CREDENTIAL_DETECTED", (
        f"Credential [{test_id}] should have rule_id=CREDENTIAL_DETECTED, got {result.rule_id}"
    )


def test_known_test_key_blocked_with_test_flag() -> None:
    """The OnGarde test key must be blocked with test=True (AC-S003-02)."""
    result = regex_scan("sk-ongarde-test-fake-key-12345")
    assert result.is_block is True
    assert result.test is True
    assert result.risk_level == RiskLevel.CRITICAL
    assert result.rule_id == "CREDENTIAL_DETECTED"


def test_known_test_key_test_flag_not_on_real_credentials() -> None:
    """A real credential must NOT have test=True."""
    result = regex_scan("sk-ant-api03-" + "A" * 93)
    assert result.is_block is True
    assert result.test is False


# ---------------------------------------------------------------------------
# Negative test cases — MUST all produce is_block=False (AC-S003-03)
# ---------------------------------------------------------------------------

CREDENTIAL_NEGATIVES = [
    (
        "plain_alphanum",
        "a" * 48,
        "Plain 48-char alphanumeric without sk- prefix",
    ),
    (
        "api_url",
        "https://api.openai.com/v1/chat/completions",
        "OpenAI API URL — no key, just domain",
    ),
    (
        "placeholder_var",
        'api_key = "your-api-key-here"',
        "Placeholder variable assignment, not a real key",
    ),
    (
        "redacted_log",
        "api_key=***REDACTED***",
        "Redacted log line — no credential content",
    ),
    (
        "generic_base64",
        "dGhpcyBpcyBqdXN0IGEgc3RyaW5n" * 2,
        "Generic base64 string (no credential prefix)",
    ),
    (
        "sk_test_not_ongarde",
        "sk-test-" + "a" * 30,
        "sk-test- prefix but not ongarde test key",
    ),
]


@pytest.mark.parametrize(
    "test_id,text,description",
    CREDENTIAL_NEGATIVES,
    ids=[x[0] for x in CREDENTIAL_NEGATIVES],
)
def test_credential_no_false_positive(test_id: str, text: str, description: str) -> None:
    """Negative cases must NOT trigger a credential block (AC-S003-03)."""
    result = regex_scan(text)
    assert result.is_block is False, (
        f"FALSE POSITIVE [{test_id}]: {description!r}\n"
        f"Input: {text!r}\n"
        f"Unexpectedly matched rule_id: {result.rule_id}"
    )


# ---------------------------------------------------------------------------
# Block rate summary assertion (AC-E002-01)
# ---------------------------------------------------------------------------


def test_credential_block_rate_100_percent() -> None:
    """Assert 100% block rate across ALL positive test cases (summary gate)."""
    failures = []
    for test_id, text, description in CREDENTIAL_POSITIVES:
        result = regex_scan(text)
        if not result.is_block:
            failures.append(f"  - {test_id}: {description}")

    assert not failures, (
        f"CREDENTIAL BLOCK RATE FAILURE — {len(failures)}/{len(CREDENTIAL_POSITIVES)} patterns missed:\n"
        + "\n".join(failures)
    )


def test_minimum_test_count() -> None:
    """CI gate: must have >= 20 positive credential tests (AC-E002-01)."""
    assert len(CREDENTIAL_POSITIVES) >= 20, (
        f"Need >= 20 credential test inputs, got {len(CREDENTIAL_POSITIVES)}"
    )
