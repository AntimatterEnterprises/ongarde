"""ULID generation utility for OnGarde — E-001-S-003.

Provides a single `generate_ulid()` function that returns a 26-character ULID
(Universally Unique Lexicographically Sortable Identifier) suitable for use as:
  - X-OnGarde-Scan-ID header value (injected on every proxied request)
  - scan_id field in audit events (AuditEvent.scan_id)
  - Correlation key in structured log entries

ULID specification (https://github.com/ulid/spec):
  - 26 characters, Crockford Base32 encoded (0-9A-HJKMNP-TV-Z)
  - 48-bit millisecond timestamp + 80-bit monotonic random component
  - Monotonically increasing within the same millisecond (no collisions)
  - URL-safe — no special characters, no padding

Uses the `python-ulid` library (see pyproject.toml) — do NOT hand-roll ULID generation.
"""

from __future__ import annotations

from ulid import ULID


def generate_ulid() -> str:
    """Generate a new ULID as a 26-character uppercase string.

    Each call returns a new, unique, monotonically increasing ULID suitable for
    use as a scan_id or tracing header value.

    Returns:
        str: A 26-character ULID string (e.g., ``"01HXXXXXXXXXXXXXXXXXXXXXX"``).
             Format: Crockford Base32 — charset ``[0-9A-HJKMNP-TV-Z]``, exactly 26 chars.

    Example::

        scan_id = generate_ulid()
        # "01KJ0JRVHYA7KX32VPN5ZSCTMV"
        assert len(scan_id) == 26
    """
    return str(ULID())
