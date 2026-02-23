"""OnGarde proxy performance benchmark — E-001-S-002.

Measures the real proxy overhead (receipt → forward to upstream) for AC-E001-04:
  ≤ 5ms p99 overhead at 100 concurrent requests (no scan, no auth).

This benchmark runs a live uvicorn server and a lightweight mock upstream
to isolate proxy machinery overhead from real LLM provider network latency.

Usage:
    cd /path/to/ongarde
    python benchmarks/bench_proxy.py

Requirements:
    - OnGarde installed in .venv
    - Ports 4242 (OnGarde) and 4299 (mock upstream) must be free

Results from initial run (2026-02-21, 2vCPU/4GB DO Droplet):
    See benchmarks/bench_proxy_results.json after first run.

Architecture reference: architecture.md §9.3, AC-E001-04
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Any

# ─── Configuration ────────────────────────────────────────────────────────────

ONGARDE_HOST = "127.0.0.1"
ONGARDE_PORT = 4242
MOCK_UPSTREAM_PORT = 4299
CONCURRENT_REQUESTS = 100
WARMUP_REQUESTS = 20
MEASURE_REQUESTS = 200  # Total measurement requests
TARGET_P99_MS = 5.0

WORKSPACE = Path(__file__).parent.parent


# ─── Mock Upstream ────────────────────────────────────────────────────────────


def _run_mock_upstream() -> None:
    """Start a minimal mock upstream in a background thread.

    Returns canned 200 JSON responses with zero processing overhead.
    Uses stdlib http.server — no FastAPI/uvicorn dependency.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class MockHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_len = int(self.headers.get("content-length", 0))
            self.rfile.read(content_len)
            body = b'{"mock": "upstream", "choices": [{"message": {"content": "ok"}}]}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass  # suppress access logs

    server = HTTPServer((ONGARDE_HOST, MOCK_UPSTREAM_PORT), MockHandler)
    server.serve_forever()


# ─── OnGarde startup ──────────────────────────────────────────────────────────


def _write_test_config() -> Path:
    """Write a temp config pointing to the mock upstream."""
    config_dir = WORKSPACE / ".ongarde-bench"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.yaml"
    config_path.write_text(
        f"""version: 1
upstream:
  openai: "http://{ONGARDE_HOST}:{MOCK_UPSTREAM_PORT}"
  anthropic: "http://{ONGARDE_HOST}:{MOCK_UPSTREAM_PORT}"
scanner:
  mode: full
"""
    )
    return config_path


# ─── Benchmark ────────────────────────────────────────────────────────────────


async def _send_batch(
    url: str, n: int, payload: bytes
) -> list[float]:
    """Send n concurrent POST requests to url. Returns list of round-trip times (ms)."""
    import httpx

    async def one_request(client: httpx.AsyncClient) -> float:
        t0 = time.perf_counter()
        await client.post(url, content=payload)
        return (time.perf_counter() - t0) * 1000

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=200),
        timeout=httpx.Timeout(10.0),
    ) as client:
        coros = [one_request(client) for _ in range(n)]
        return list(await asyncio.gather(*coros))


async def run_benchmark() -> dict[str, Any]:
    """Run the full benchmark and return results."""
    import httpx

    url = f"http://{ONGARDE_HOST}:{ONGARDE_PORT}/v1/chat/completions"
    payload = json.dumps(
        {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Bench test"}],
        }
    ).encode()

    print(f"\n{'='*60}")
    print(f"OnGarde Proxy Benchmark — E-001-S-002")
    print(f"{'='*60}")
    print(f"Target: p99 ≤ {TARGET_P99_MS}ms at {CONCURRENT_REQUESTS} concurrent")
    print(f"OnGarde: http://{ONGARDE_HOST}:{ONGARDE_PORT}")
    print(f"Mock upstream: http://{ONGARDE_HOST}:{MOCK_UPSTREAM_PORT}")
    print()

    # Wait for OnGarde to be ready
    print("Waiting for OnGarde to start...")
    async with httpx.AsyncClient() as client:
        for _ in range(30):
            try:
                resp = await client.get(f"http://{ONGARDE_HOST}:{ONGARDE_PORT}/health")
                if resp.status_code == 200:
                    print("✓ OnGarde ready")
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            print("✗ OnGarde did not start within 15 seconds")
            sys.exit(1)

    # Warm up
    print(f"\nWarm-up: {WARMUP_REQUESTS} sequential requests...")
    await _send_batch(url, WARMUP_REQUESTS, payload)
    print("✓ Warm-up complete")

    # Measurement
    print(f"\nMeasurement: {MEASURE_REQUESTS} concurrent × batches...")
    all_latencies: list[float] = []

    for batch_num in range(3):
        latencies = await _send_batch(url, MEASURE_REQUESTS // 3, payload)
        all_latencies.extend(latencies)
        p50 = statistics.median(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        print(f"  Batch {batch_num + 1}: p50={p50:.2f}ms p99={p99:.2f}ms")

    # Stats
    all_sorted = sorted(all_latencies)
    n = len(all_sorted)
    p50 = all_sorted[n // 2]
    p95 = all_sorted[int(n * 0.95)]
    p99 = all_sorted[int(n * 0.99)]
    p999 = all_sorted[int(n * 0.999)] if n >= 1000 else all_sorted[-1]
    mean_ms = statistics.mean(all_latencies)
    stdev_ms = statistics.stdev(all_latencies) if len(all_latencies) > 1 else 0.0

    print(f"\n{'─'*40}")
    print(f"Results ({n} total requests, {CONCURRENT_REQUESTS} concurrent):")
    print(f"  mean  = {mean_ms:.2f}ms  (±{stdev_ms:.2f}ms)")
    print(f"  p50   = {p50:.2f}ms")
    print(f"  p95   = {p95:.2f}ms")
    print(f"  p99   = {p99:.2f}ms  {'✓ PASS' if p99 <= TARGET_P99_MS else '✗ FAIL'}")
    print(f"  p99.9 = {p999:.2f}ms")
    print(f"  max   = {max(all_latencies):.2f}ms")
    print(f"{'─'*40}")

    if p99 <= TARGET_P99_MS:
        print(f"\n✓ AC-E001-04 PASSED: p99 {p99:.2f}ms ≤ {TARGET_P99_MS}ms")
    else:
        print(f"\n✗ AC-E001-04 FAILED: p99 {p99:.2f}ms > {TARGET_P99_MS}ms")

    results = {
        "benchmark": "bench_proxy",
        "story": "E-001-S-002",
        "ac": "AC-E001-04",
        "target_p99_ms": TARGET_P99_MS,
        "concurrent_requests": CONCURRENT_REQUESTS,
        "total_requests": n,
        "mean_ms": round(mean_ms, 3),
        "stdev_ms": round(stdev_ms, 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(p99, 3),
        "p999_ms": round(p999, 3),
        "max_ms": round(max(all_latencies), 3),
        "passed": p99 <= TARGET_P99_MS,
    }
    return results


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    """Start mock upstream, start OnGarde, run benchmark, print results."""
    # Start mock upstream in background thread
    mock_thread = threading.Thread(target=_run_mock_upstream, daemon=True)
    mock_thread.start()
    print(f"Mock upstream started on port {MOCK_UPSTREAM_PORT}")

    # Write test config
    config_path = _write_test_config()

    # Start OnGarde as a subprocess
    env = os.environ.copy()
    env["JSON_LOGS"] = "false"
    venv_python = WORKSPACE / ".venv" / "bin" / "python"
    uvicorn_cmd = [
        str(venv_python), "-m", "uvicorn",
        "app.main:app",
        "--host", ONGARDE_HOST,
        "--port", str(ONGARDE_PORT),
        "--limit-concurrency", "100",
        "--backlog", "50",
        "--timeout-keep-alive", "5",
    ]

    ongarde_env = env.copy()
    ongarde_env["ONGARDE_CONFIG_PATH"] = str(config_path)

    print(f"Starting OnGarde on port {ONGARDE_PORT}...")
    proc = subprocess.Popen(
        uvicorn_cmd,
        cwd=str(WORKSPACE),
        env=ongarde_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        results = asyncio.run(run_benchmark())

        # Save results
        results_path = WORKSPACE / "benchmarks" / "bench_proxy_results.json"
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {results_path}")

        sys.exit(0 if results["passed"] else 1)

    finally:
        proc.terminate()
        proc.wait(timeout=5)
        # Clean up temp config
        config_path.unlink(missing_ok=True)
        config_path.parent.rmdir()


if __name__ == "__main__":
    main()
