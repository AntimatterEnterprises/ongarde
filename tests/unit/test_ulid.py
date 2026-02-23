"""Unit tests for E-001-S-003: ULID generation utility (app/utils/ulid.py).

Covers ACs:
  AC-E001-01 (partial): X-OnGarde-Scan-ID value is a valid ULID (26 chars, correct charset)
  Story AC-7: generate_ulid() returns a valid ULID — monotonically increasing, URL-safe, 26 chars
  Story AC-8: 1,000 generated ULIDs are all unique and all pass format validation
  Story AC-9: ULIDs generated in the same millisecond are still unique (monotonic random)
"""

from __future__ import annotations

import re
import threading
import time

import pytest

from app.utils.ulid import generate_ulid

# ─── ULID format constants ─────────────────────────────────────────────────────

# Crockford Base32 charset: 0-9 and A-Z, excluding I, L, O, U
ULID_CHARSET = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ULID_LENGTH = 26


# ─── Basic format tests ────────────────────────────────────────────────────────


def test_generate_ulid_returns_string() -> None:
    """generate_ulid() must return a plain str."""
    result = generate_ulid()
    assert isinstance(result, str)


def test_generate_ulid_length() -> None:
    """ULID must be exactly 26 characters (AC-7, AC-8)."""
    result = generate_ulid()
    assert len(result) == ULID_LENGTH, f"Expected 26 chars, got {len(result)}: {result!r}"


def test_generate_ulid_charset() -> None:
    """ULID must use Crockford Base32 charset only (AC-7)."""
    result = generate_ulid()
    assert ULID_CHARSET.match(result), (
        f"ULID {result!r} contains invalid characters. "
        "Expected only [0-9A-HJKMNP-TV-Z]."
    )


def test_generate_ulid_url_safe() -> None:
    """ULID must be URL-safe (no special characters, no padding) (AC-7)."""
    result = generate_ulid()
    # No characters that would require URL encoding
    url_safe = re.compile(r"^[A-Z0-9]+$")
    assert url_safe.match(result), f"ULID {result!r} is not URL-safe"


def test_generate_ulid_no_lowercase() -> None:
    """generate_ulid() must return uppercase (standard ULID output from python-ulid)."""
    result = generate_ulid()
    assert result == result.upper(), f"ULID {result!r} contains lowercase characters"


# ─── Uniqueness tests ──────────────────────────────────────────────────────────


def test_generate_ulid_unique_1000() -> None:
    """1,000 generated ULIDs must all be unique (AC-8)."""
    ulids = [generate_ulid() for _ in range(1000)]
    assert len(set(ulids)) == 1000, (
        f"Duplicate ULIDs detected among 1,000 generated: "
        f"{len(ulids) - len(set(ulids))} duplicates"
    )


def test_generate_ulid_all_valid_format_1000() -> None:
    """All 1,000 generated ULIDs must pass ULID format validation (AC-8)."""
    ulids = [generate_ulid() for _ in range(1000)]
    invalid = [u for u in ulids if not ULID_CHARSET.match(u)]
    assert not invalid, f"Invalid ULIDs found: {invalid[:5]}"


def test_generate_ulid_all_correct_length_1000() -> None:
    """All 1,000 generated ULIDs must be exactly 26 characters (AC-8)."""
    ulids = [generate_ulid() for _ in range(1000)]
    wrong_length = [u for u in ulids if len(u) != ULID_LENGTH]
    assert not wrong_length, f"ULIDs with wrong length: {wrong_length[:5]}"


# ─── Monotonicity tests ────────────────────────────────────────────────────────


def test_generate_ulid_monotonic_same_millisecond() -> None:
    """ULIDs generated in the same millisecond must still be unique (AC-9).

    The ULID monotonic random component ensures uniqueness within a millisecond.
    We freeze time by generating many ULIDs rapidly; python-ulid guarantees
    lexicographic monotonicity within a millisecond.
    """
    # Generate a burst of ULIDs in rapid succession
    # (some will share the same millisecond timestamp)
    burst = [generate_ulid() for _ in range(100)]
    assert len(set(burst)) == 100, (
        f"Duplicate ULIDs in rapid-fire burst: "
        f"{len(burst) - len(set(burst))} duplicates"
    )


def test_generate_ulid_lexicographic_order() -> None:
    """ULIDs generated sequentially must be lexicographically ordered.

    Because the timestamp component is the most-significant portion and increases
    monotonically, ULIDs generated in sequence should sort in generation order.
    This is a ULID spec property: enables range queries over sorted sets.
    """
    # Generate with small delay between batches to ensure timestamp increments
    first_batch = [generate_ulid() for _ in range(10)]
    time.sleep(0.002)  # 2ms — guarantees timestamp advances
    second_batch = [generate_ulid() for _ in range(10)]

    # All second-batch ULIDs must be lexicographically greater than all first-batch ULIDs
    assert all(
        b > a for a in first_batch for b in second_batch
    ), "ULIDs are not lexicographically ordered across timestamps"


# ─── Thread safety ────────────────────────────────────────────────────────────


def test_generate_ulid_thread_safe() -> None:
    """generate_ulid() must produce unique values across multiple threads."""
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        ulids = [generate_ulid() for _ in range(50)]
        with lock:
            results.extend(ulids)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 500
    assert len(set(results)) == 500, (
        f"Thread-safety failure: {len(results) - len(set(results))} duplicate ULIDs "
        "across 10 threads × 50 ULIDs"
    )


# ─── Scan ID header compatibility ─────────────────────────────────────────────


def test_generate_ulid_valid_as_http_header_value() -> None:
    """ULID must be usable as an HTTP header value without encoding.

    X-OnGarde-Scan-ID is set directly as a header value — must not contain
    characters that require special handling in HTTP headers.
    """
    ulid = generate_ulid()
    # HTTP header values must be printable ASCII only
    assert all(0x20 <= ord(c) <= 0x7E for c in ulid), (
        f"ULID {ulid!r} contains non-printable or non-ASCII characters"
    )
    # Must not contain header-special characters
    forbidden = set("\r\n\x00:")
    assert not any(c in forbidden for c in ulid), (
        f"ULID {ulid!r} contains forbidden header characters"
    )
