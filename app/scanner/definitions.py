"""Pattern definitions for the regex fast-path scanner — E-002-S-001.

All patterns are pre-compiled at module load time using google-re2.
NO pattern compilation happens per-request, per-call, or lazily.

IMPORT RULES:
  - ``import re2`` ONLY — ``import re`` is PROHIBITED in this file and any app/scanner/ file.
  - CI lint gate: grep -r "^import re$|^from re import|^import re " app/scanner/
    will fail the build if a bare ``import re`` is found.

Architecture reference: architecture.md §2.1, §2.2, §10.1
Story: E-002-S-001
"""

from __future__ import annotations

# google-re2 — NOT stdlib re. Aliased as re for readability.
# PROHIBITED: import re   ← NEVER. Not here, not in any app/scanner/ file.
import re2  # noqa: F401 — re2 alias intentional

from dataclasses import dataclass
from typing import Any

from app.models.scan import RiskLevel


# ---------------------------------------------------------------------------
# PatternEntry dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PatternEntry:
    """A single compiled security pattern with metadata.

    Fields:
        pattern:    Pre-compiled re2 pattern object. Compiled at module load time.
        rule_id:    Identifier for this rule (e.g. ``"CREDENTIAL_DETECTED"``).
        risk_level: Risk classification for this match (CRITICAL, HIGH, MEDIUM, LOW).
        slug:       Kebab-case slug used in ``suppression_hint`` generation.
                    (e.g. ``"openai-api-key"``)
        test:       True only for the known OnGarde test credential.
                    Test matches do not decrement the monthly request quota (E-005).
    """
    pattern: Any           # re2._Regexp — pre-compiled at module load
    rule_id: str
    risk_level: RiskLevel
    slug: str              # kebab-case, used in suppression_hint
    test: bool = False     # True only for known test credential


# ===========================================================================
# CREDENTIAL PATTERNS
# COMPILED AT MODULE LOAD — never per-request
# ===========================================================================

# The OnGarde test credential used by the onboarding wizard (E-007).
# MUST be the FIRST entry evaluated — exact match before broader regex patterns.
TEST_CREDENTIAL_PATTERN = PatternEntry(
    pattern=re2.compile(r'sk-ongarde-test-fake-key-12345'),
    rule_id="CREDENTIAL_DETECTED",
    risk_level=RiskLevel.CRITICAL,
    slug="ongarde-test-key",
    test=True,
)

CREDENTIAL_PATTERNS: list[PatternEntry] = [
    # ─── OpenAI API keys ─────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'sk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="openai-api-key-classic",
    ),
    PatternEntry(
        pattern=re2.compile(r'sk-proj-[a-zA-Z0-9_-]{50,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="openai-project-key",
    ),
    PatternEntry(
        pattern=re2.compile(r'sk-[a-zA-Z0-9]{48}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="openai-api-key",
    ),
    # ─── Anthropic ────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'sk-ant-api03-[a-zA-Z0-9_-]{93}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="anthropic-api-key",
    ),
    # ─── AWS ──────────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="aws-access-key-id",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)aws.{0,20}secret.{0,20}[=:]\s*[a-zA-Z0-9/+]{40}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="aws-secret-access-key",
    ),
    # ─── GitHub tokens ────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'gh[pousr]_[a-zA-Z0-9]{36}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="github-access-token",
    ),
    PatternEntry(
        pattern=re2.compile(r'github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="github-fine-grained-pat",
    ),
    # ─── Generic Bearer token ─────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'Bearer\s+[a-zA-Z0-9._\-+/=]{64,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="bearer-token",
    ),
    # ─── Stripe ───────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'sk_live_[a-zA-Z0-9]{24,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="stripe-live-secret-key",
    ),
    PatternEntry(
        pattern=re2.compile(r'rk_live_[a-zA-Z0-9]{24,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="stripe-restricted-key",
    ),
    # ─── HuggingFace ──────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'hf_[a-zA-Z0-9]{34,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="huggingface-token",
    ),
    # ─── Slack ────────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'xoxb-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="slack-bot-token",
    ),
    PatternEntry(
        pattern=re2.compile(r'xapp-[0-9]-[a-zA-Z0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{64,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="slack-app-token",
    ),
    # ─── Twilio ───────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'AC[a-f0-9]{32}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="twilio-account-sid",
    ),
    # ─── Google API Key ───────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'AIza[0-9A-Za-z_-]{35}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="google-api-key",
    ),
    # ─── SendGrid ─────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'SG\.[a-zA-Z0-9._]{22,}\.[a-zA-Z0-9._]{43,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="sendgrid-api-key",
    ),
    # ─── Mailgun ──────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'key-[a-z0-9]{32}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="mailgun-private-key",
    ),
    # ─── NPM ──────────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'npm_[a-zA-Z0-9]{36}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="npm-token",
    ),
    # ─── PyPI ─────────────────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'pypi-[a-zA-Z0-9_-]{50,}'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="pypi-token",
    ),
    # ─── PEM private keys ─────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'),
        rule_id="CREDENTIAL_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="pem-private-key",
    ),
]


# ===========================================================================
# DANGEROUS COMMAND PATTERNS
# COMPILED AT MODULE LOAD — never per-request
# ===========================================================================

DANGEROUS_COMMAND_PATTERNS: list[PatternEntry] = [
    # ─── Shell destructors ────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'(?i)\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="rm-rf",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="rm-fr",
    ),
    PatternEntry(
        # sudo at start of command or after command separator (not in middle of sentences)
        pattern=re2.compile(r'(?m)(?:^|[;\n|&])\s*sudo\s+'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="sudo-usage",
    ),
    PatternEntry(
        pattern=re2.compile(r'dd\s+if='),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="dd-disk-copy",
    ),
    PatternEntry(
        pattern=re2.compile(r'mkfs\.'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="mkfs-format",
    ),
    PatternEntry(
        pattern=re2.compile(r'chmod\s+(777|-R\s+777|0777)'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="chmod-world-writable",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)curl.+\|\s*(bash|sh)'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="curl-pipe-execute",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)wget.+\|\s*(bash|sh)'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="wget-pipe-execute",
    ),
    PatternEntry(
        pattern=re2.compile(r':\s*\(\s*\)\s*\{.*\}'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="fork-bomb",
    ),
    PatternEntry(
        pattern=re2.compile(r'>\s*/dev/sda\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="direct-disk-write",
    ),
    # ─── SQL destructors ─────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'(?i)\bDROP\s+TABLE\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="sql-drop-table",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\bDROP\s+DATABASE\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="sql-drop-database",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\bTRUNCATE\s+(TABLE\s+)?\w'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="sql-truncate",
    ),
    PatternEntry(
        # DELETE FROM without WHERE clause (ends with ; or EOL)
        pattern=re2.compile(r'(?i)\bDELETE\s+FROM\s+\w+\s*;'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="sql-delete-no-where",
    ),
    PatternEntry(
        # DELETE FROM without WHERE clause (EOL variant)
        pattern=re2.compile(r'(?im)\bDELETE\s+FROM\s+\w+\s*$'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.CRITICAL,
        slug="sql-delete-no-where-eol",
    ),
    # ─── Unauthorized path access ─────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'\.ssh/id_rsa\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="ssh-private-key-path",
    ),
    PatternEntry(
        pattern=re2.compile(r'\.ssh/authorized_keys\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="ssh-authorized-keys-path",
    ),
    PatternEntry(
        pattern=re2.compile(r'/etc/passwd\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="etc-passwd-path",
    ),
    PatternEntry(
        pattern=re2.compile(r'/etc/shadow\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="etc-shadow-path",
    ),
    PatternEntry(
        pattern=re2.compile(r'/etc/sudoers\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="etc-sudoers-path",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)credentials\.(json|yaml|yml|csv|txt)\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="credentials-file-path",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\.env\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="dotenv-file-path",
    ),
    PatternEntry(
        pattern=re2.compile(r'~/\.aws/credentials\b'),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="aws-credentials-path",
    ),
    # ─── Code execution ───────────────────────────────────────────────────
    PatternEntry(
        pattern=re2.compile(r'\beval\s*\('),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="eval-execution",
    ),
    PatternEntry(
        pattern=re2.compile(r'\bexec\s*\('),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="exec-execution",
    ),
    PatternEntry(
        pattern=re2.compile(r'\b__import__\s*\('),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="import-injection",
    ),
    PatternEntry(
        pattern=re2.compile(r'\bos\.system\s*\('),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="os-system-execution",
    ),
    PatternEntry(
        pattern=re2.compile(r'\bsubprocess\.(call|run|Popen)\s*\('),
        rule_id="DANGEROUS_COMMAND_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="subprocess-execution",
    ),
]


# ===========================================================================
# PROMPT INJECTION PATTERNS
# COMPILED AT MODULE LOAD — never per-request
# All patterns use (?i) inline flag for case-insensitivity
# ===========================================================================

PROMPT_INJECTION_PATTERNS: list[PatternEntry] = [
    PatternEntry(
        pattern=re2.compile(r'(?i)ignore (all )?(previous|prior|above) instructions'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="ignore-previous-instructions",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)disregard (your )?(system prompt|previous instructions|all instructions)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="disregard-instructions",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\bDAN\b.{0,50}(mode|activate|enable)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="dan-mode-activation",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)do anything now'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="do-anything-now",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)you are no longer (bound by|restricted by|limited to)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="no-longer-restricted",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)your new (instructions|directive|purpose|role) (are|is)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="new-instructions-override",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)act as (an? )?(unrestricted|jailbroken|different) (ai|assistant|model|llm|version)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="act-as-unrestricted",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)forget everything (you|i|we) (have been told|have said|have discussed|said|told)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="forget-instructions",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)pretend (you|that) (are|have) no (restrictions|limits|guidelines)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="pretend-no-restrictions",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)override (your )?(safety|content|ethical) (filters|guidelines|restrictions)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="override-safety-filters",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)enable (developer|god|admin|root|debug|jailbreak) mode'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="enable-special-mode",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\bJAILBREAK\s*:'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="jailbreak-label",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\bSYSTEM OVERRIDE\s*:'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="system-override-label",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\bSUDO MODE\s*:'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="sudo-mode-label",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\bPROMPT INJECTION\s*:'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="prompt-injection-label",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)repeat (the|your) (system|base) prompt (back|verbatim|word for word)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="repeat-system-prompt",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)(what (is|was) (your|the)|output (your|the) (raw|full)) (system prompt|instructions)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="extract-system-prompt",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)\[(?:NEW INSTRUCTIONS|SYSTEM UPDATE|ADMIN OVERRIDE|SYSTEM MESSAGE)\]'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="injected-instruction-block",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)from now on (you will|you must|you shall|respond as)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="from-now-on-directive",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)you are (an? )?(evil|malicious|harmful|dangerous|unrestricted) (ai|assistant|bot)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="evil-ai-role-assignment",
    ),
    PatternEntry(
        pattern=re2.compile(r'(?i)translate (the )?above (content|text|prompt) to'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.MEDIUM,
        slug="translate-above-prompt",
    ),
    PatternEntry(
        # Role confusion: "You are now in X mode" where X suggests jailbreak
        pattern=re2.compile(r'(?i)you are now (in )?(DAN|jailbreak|god|unrestricted|developer|admin) mode'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.HIGH,
        slug="role-confusion-mode",
    ),
    PatternEntry(
        # Encoded injection attempts
        pattern=re2.compile(r'\batob\s*\('),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.MEDIUM,
        slug="encoded-injection-atob",
    ),
    PatternEntry(
        # "start fresh" / "start over" with no restrictions context
        pattern=re2.compile(r'(?i)(start fresh|start over).{0,30}(no restrictions|without restrictions|ignore)'),
        rule_id="PROMPT_INJECTION_DETECTED",
        risk_level=RiskLevel.MEDIUM,
        slug="start-fresh-no-restrictions",
    ),
]


# ===========================================================================
# PII FAST-PATH PATTERNS (pre-filter for Full mode; sole PII mechanism in Lite mode)
# COMPILED AT MODULE LOAD — never per-request
# ===========================================================================

PII_FAST_PATH_PATTERNS: list[PatternEntry] = [
    PatternEntry(
        # US Social Security Number — 3-2-4 format, simplified (re2 has no lookaheads)
        pattern=re2.compile(r'\b\d{3}[-. ]?\d{2}[-. ]?\d{4}\b'),
        rule_id="PII_DETECTED_US_SSN",
        risk_level=RiskLevel.HIGH,
        slug="pii-us-ssn",
    ),
    PatternEntry(
        # Credit card — Visa (16d), MC (16d), Amex (15d), Discover (16d)
        pattern=re2.compile(
            r'\b(?:4[0-9]{12}(?:[0-9]{3})?'
            r'|5[1-5][0-9]{14}'
            r'|6(?:011|5[0-9]{2})[0-9]{12}'
            r'|3[47][0-9]{13}'
            r'|3(?:0[0-5]|[68][0-9])[0-9]{11})'
            r'(?:[-\s]?[0-9]{4}){0,3}\b'
        ),
        rule_id="PII_DETECTED_CREDIT_CARD",
        risk_level=RiskLevel.HIGH,
        slug="pii-credit-card",
    ),
    PatternEntry(
        # Email address — RFC 5321 practical pattern
        pattern=re2.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b'),
        rule_id="PII_DETECTED_EMAIL",
        risk_level=RiskLevel.HIGH,
        slug="pii-email",
    ),
    PatternEntry(
        # US Phone — (555) 555-5555, 555-555-5555, +1 555 555 5555, etc.
        pattern=re2.compile(
            r'(?:\+1[-.\s]?)?(?:\([2-9][0-9]{2}\)|[2-9][0-9]{2})[-.\s]?[2-9][0-9]{2}[-.\s]?[0-9]{4}'
        ),
        rule_id="PII_DETECTED_PHONE_US",
        risk_level=RiskLevel.HIGH,
        slug="pii-phone-us",
    ),
    PatternEntry(
        # BTC P2PKH (1...) and P2SH (3...) addresses
        pattern=re2.compile(r'\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b'),
        rule_id="PII_DETECTED_CRYPTO",
        risk_level=RiskLevel.HIGH,
        slug="pii-crypto-btc-p2pkh-p2sh",
    ),
    PatternEntry(
        # BTC Bech32 (bc1...)
        pattern=re2.compile(r'\bbc1[ac-hj-np-z02-9]{6,87}\b'),
        rule_id="PII_DETECTED_CRYPTO",
        risk_level=RiskLevel.HIGH,
        slug="pii-crypto-btc-bech32",
    ),
    PatternEntry(
        # ETH/EVM (0x + 40 hex)
        pattern=re2.compile(r'\b0x[a-fA-F0-9]{40}\b'),
        rule_id="PII_DETECTED_CRYPTO",
        risk_level=RiskLevel.HIGH,
        slug="pii-crypto-eth-evm",
    ),
    PatternEntry(
        # Litecoin (L... or M...)
        pattern=re2.compile(r'\b[LM3][a-km-zA-HJ-NP-Z1-9]{26,33}\b'),
        rule_id="PII_DETECTED_CRYPTO",
        risk_level=RiskLevel.HIGH,
        slug="pii-crypto-litecoin",
    ),
    PatternEntry(
        # XRP (r + 24-34 base58 chars)
        pattern=re2.compile(r'\br[0-9a-zA-Z]{24,34}\b'),
        rule_id="PII_DETECTED_CRYPTO",
        risk_level=RiskLevel.HIGH,
        slug="pii-crypto-xrp",
    ),
]


# ===========================================================================
# ALL_PATTERNS — concatenation of all four groups (used by benchmark and tests)
# ===========================================================================

ALL_PATTERNS: list[PatternEntry] = (
    [TEST_CREDENTIAL_PATTERN]
    + CREDENTIAL_PATTERNS
    + DANGEROUS_COMMAND_PATTERNS
    + PROMPT_INJECTION_PATTERNS
    + PII_FAST_PATH_PATTERNS
)
