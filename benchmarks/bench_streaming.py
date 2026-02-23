#!/usr/bin/env python3
"""Benchmark: StreamingScanner window scan latency.

Validates AC-E004-02: ≤ 0.5ms p99 per 512-char window scan.

Usage::

    cd /path/to/ongarde
    .venv/bin/python benchmarks/bench_streaming.py

Results are saved to benchmarks/streaming_results.json.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Ensure the venv is active or imports are available
try:
    from app.scanner.streaming_scanner import StreamingScanner, WINDOW_SIZE
    from app.scanner.regex_engine import regex_scan
except ImportError as e:
    print(f"Import error: {e}")
    print("Run from project root with venv active: .venv/bin/python benchmarks/bench_streaming.py")
    sys.exit(1)


# ─── Benchmark Configuration ──────────────────────────────────────────────────

ITERATIONS = 100
P99_GATE_MS = 0.5  # AC-E004-02: ≤ 0.5ms p99

# Clean text window (no threat) — represents normal streaming content
CLEAN_WINDOW = (
    "The quick brown fox jumps over the lazy dog. "
    "In a land far away, a wizard named Gandalf discovered an ancient artifact. "
    "The artifact was said to hold the power to reshape the world. "
    "Many heroes had sought it before, none had returned. "
    "Yet our hero pressed onward, determined and resolute. "
    "The path wound through dark forests and over treacherous mountains. "
    "Each step brought new challenges and new wisdom. "
    "Finally, after many days of travel, the artifact came into view. "
    "It glowed with an ethereal light, pulsing like a heartbeat."
)[:WINDOW_SIZE]

# Window containing a credential (should trigger BLOCK)
CREDENTIAL_WINDOW = (
    "The deployment configuration has been updated. "
    "API_KEY=sk-abc123def456ghi789jkl012mno345pqr "
    "DATABASE_URL=postgresql://user:pass@localhost/db "
    "Please ensure all services are restarted after applying changes. "
    "The monitoring system will alert on any anomalies detected. "
    "Backup credentials are stored in the vault for recovery purposes. "
    "System administrators should rotate keys every 90 days as policy."
)[:WINDOW_SIZE]

# Window with an OpenAI-style API key
OPENAI_KEY_WINDOW = (
    "Remember to set your sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIj "
    "in the environment before running the application. "
    "This key should never be committed to version control. "
    "Use environment variables or a secrets manager for production deployments."
)[:WINDOW_SIZE]


def benchmark_window_scan(window_text: str, label: str) -> dict:
    """Benchmark a single window type over ITERATIONS runs."""
    latencies = []

    for i in range(ITERATIONS):
        scanner = StreamingScanner(scan_id=f"bench-{i:04d}")
        t0 = time.perf_counter()
        scanner._do_window_scan_for_bench(window_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)

    latencies.sort()
    p50 = latencies[int(ITERATIONS * 0.50)]
    p95 = latencies[int(ITERATIONS * 0.95)]
    p99_idx = min(int(ITERATIONS * 0.99), ITERATIONS - 1)
    p99 = latencies[p99_idx]
    avg = statistics.mean(latencies)
    max_lat = max(latencies)

    result = {
        "label": label,
        "iterations": ITERATIONS,
        "window_size_chars": len(window_text),
        "avg_ms": round(avg, 4),
        "p50_ms": round(p50, 4),
        "p95_ms": round(p95, 4),
        "p99_ms": round(p99, 4),
        "max_ms": round(max_lat, 4),
        "p99_gate_ms": P99_GATE_MS,
        "p99_pass": p99 <= P99_GATE_MS,
    }

    status = "✅ PASS" if result["p99_pass"] else "❌ FAIL"
    print(f"  {status} [{label}] p99={p99:.4f}ms p50={p50:.4f}ms avg={avg:.4f}ms max={max_lat:.4f}ms")
    return result


def benchmark_via_add_content() -> dict:
    """Benchmark using the public add_content() API (full overhead)."""
    latencies = []

    for i in range(ITERATIONS):
        scanner = StreamingScanner(scan_id=f"bench-ac-{i:04d}")
        # Build a clean 512-char content in two add_content() calls
        chunk_a = CLEAN_WINDOW[:256]
        chunk_b = CLEAN_WINDOW[256:]

        t0 = time.perf_counter()
        result1 = scanner.add_content(chunk_a)
        result2 = scanner.add_content(chunk_b)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)

    latencies.sort()
    p99_idx = min(int(ITERATIONS * 0.99), ITERATIONS - 1)
    p99 = latencies[p99_idx]
    avg = statistics.mean(latencies)

    result = {
        "label": "add_content_api_full_window",
        "iterations": ITERATIONS,
        "window_size_chars": 512,
        "avg_ms": round(avg, 4),
        "p99_ms": round(p99, 4),
        "p99_gate_ms": P99_GATE_MS,
        "p99_pass": p99 <= P99_GATE_MS,
    }

    status = "✅ PASS" if result["p99_pass"] else "❌ FAIL"
    print(f"  {status} [add_content full window] p99={p99:.4f}ms avg={avg:.4f}ms")
    return result


def main() -> None:
    print("=" * 70)
    print("OnGarde StreamingScanner Benchmark — AC-E004-02: ≤ 0.5ms p99")
    print("=" * 70)
    print(f"Iterations per test: {ITERATIONS}")
    print(f"Window size: {WINDOW_SIZE} chars")
    print(f"P99 gate: ≤ {P99_GATE_MS}ms")
    print()

    # Add the bench helper method to StreamingScanner temporarily
    def _do_window_scan_for_bench(self, window_text: str):
        self.window_buffer = window_text
        return self._do_window_scan()

    StreamingScanner._do_window_scan_for_bench = _do_window_scan_for_bench  # type: ignore

    print("Running benchmarks...")
    results = []

    # Benchmark 1: Clean window (no threat)
    results.append(benchmark_window_scan(CLEAN_WINDOW.ljust(WINDOW_SIZE)[:WINDOW_SIZE], "clean_window_pass"))

    # Benchmark 2: Window with credential (BLOCK path)
    results.append(benchmark_window_scan(CREDENTIAL_WINDOW.ljust(WINDOW_SIZE)[:WINDOW_SIZE], "credential_window_block"))

    # Benchmark 3: Window with OpenAI API key style
    results.append(benchmark_window_scan(OPENAI_KEY_WINDOW.ljust(WINDOW_SIZE)[:WINDOW_SIZE], "openai_key_window"))

    # Benchmark 4: via public add_content API
    results.append(benchmark_via_add_content())

    print()

    # Summary
    all_pass = all(r["p99_pass"] for r in results)
    overall = "✅ ALL PASS" if all_pass else "❌ SOME FAILURES"
    print(f"Overall: {overall}")

    max_p99 = max(r["p99_ms"] for r in results)
    print(f"Worst p99: {max_p99:.4f}ms (gate: {P99_GATE_MS}ms)")

    # Save results
    output = {
        "benchmark": "streaming_scanner_window_scan",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "iterations": ITERATIONS,
        "p99_gate_ms": P99_GATE_MS,
        "all_pass": all_pass,
        "results": results,
    }

    output_path = Path(__file__).parent / "streaming_results.json"
    with output_path.open("w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {output_path}")

    # Exit with error if any benchmark failed
    if not all_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
