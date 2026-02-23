#!/usr/bin/env python3
"""
OnGarde Presidio Benchmark Suite — Part 2
Winston, Software Architect — February 2026

Follow-up benchmarks:
1. ProcessPoolExecutor with proper initializer (pre-loaded model)
2. Short typical prompts (100-400 chars) — most common LLM usage
3. Regex-only fast path latency (to establish the floor)
4. Recommended architecture validation: regex sync + Presidio async timeout
5. Full results with per-char latency scaling analysis
"""

import time
import statistics
import asyncio
import concurrent.futures
import json
import random
import sys
import re
from typing import List

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (p / 100) * (len(sorted_data) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_data):
        return sorted_data[-1]
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


def measure_ms(fn, *args, **kwargs) -> float:
    start = time.perf_counter()
    fn(*args, **kwargs)
    end = time.perf_counter()
    return (end - start) * 1000.0


SAMPLE_PARAGRAPHS = [
    "Hello, my name is John Smith and I work at Acme Corporation in New York. "
    "Please send the invoice to john.smith@acme.com or call me at 555-867-5309. "
    "My social security number is 123-45-6789 and my credit card is 4532015112830366. ",
    "The quarterly report shows revenue growth of 12% across all divisions. "
    "Our engineering team based in San Francisco has delivered the new API framework. "
    "The deployment is scheduled for March 15th and will affect approximately 10,000 users. ",
    "I need to transfer funds from account 9876543210 to IBAN GB82WEST12345698765432. "
    "The Bitcoin address for the transaction is 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2. "
    "Please confirm receipt at billing@example.org or via our secure portal. ",
]


def generate_text(target_chars: int) -> str:
    result = []
    current = 0
    while current < target_chars:
        para = random.choice(SAMPLE_PARAGRAPHS)
        result.append(para)
        current += len(para)
    return " ".join(result)[:target_chars]


# ─────────────────────────────────────────────────────────────────────────────
# Process pool initializer — the CORRECT way to use Presidio with ProcessPool
# ─────────────────────────────────────────────────────────────────────────────

_process_analyzer = None

def process_pool_init():
    """Initialize the analyzer in each worker process once."""
    global _process_analyzer
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    }
    provider = NlpEngineProvider(nlp_configuration=configuration)
    nlp_engine = provider.create_engine()
    _process_analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
    # Warm up the analyzer
    _process_analyzer.analyze(text="warmup hello world", language="en",
                              entities=["CREDIT_CARD", "EMAIL_ADDRESS", "PHONE_NUMBER"])


def process_pool_scan(text: str, entities) -> int:
    """Scan text in pool worker — returns number of results (simulates work)."""
    global _process_analyzer
    results = _process_analyzer.analyze(text=text, language="en", entities=entities)
    return len(results)


# ─────────────────────────────────────────────────────────────────────────────
# Regex fast path
# ─────────────────────────────────────────────────────────────────────────────

CREDENTIAL_PATTERNS = [
    # OpenAI API key
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),
    # Generic API key patterns
    re.compile(r'(?i)api[_-]?key["\s:=]+[a-zA-Z0-9]{16,}'),
    # Email addresses
    re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'),
    # Credit card (Luhn-valid-ish patterns)
    re.compile(r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11})\b'),
    # SSN
    re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    # Phone number
    re.compile(r'\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
    # Bitcoin address
    re.compile(r'\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b'),
    # JWT token
    re.compile(r'eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+'),
    # AWS key
    re.compile(r'AKIA[0-9A-Z]{16}'),
    # Dangerous commands
    re.compile(r'\b(?:rm\s+-rf|DROP\s+TABLE|exec\s*\(|eval\s*\(|__import__)\b'),
    # Private key header
    re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'),
]


def regex_scan(text: str) -> list:
    """Fast regex-only scan. Returns list of match descriptions."""
    matches = []
    for pattern in CREDENTIAL_PATTERNS:
        m = pattern.search(text)
        if m:
            matches.append({"pattern": pattern.pattern[:30], "start": m.start()})
    return matches


# ─────────────────────────────────────────────────────────────────────────────
# BENCHMARK 1: Proper ProcessPoolExecutor with initializer
# ─────────────────────────────────────────────────────────────────────────────

async def bench_proper_process_pool() -> dict:
    """Test ProcessPoolExecutor with proper initializer — model stays loaded."""
    print("\n[B1] ProcessPoolExecutor with proper initializer benchmark...")
    print("     (Simulating 2vCPU: 1 worker process, model pre-loaded)")

    text_1000 = generate_text(1000)
    text_500 = generate_text(500)
    text_300 = generate_text(300)
    entities = ["CREDIT_CARD", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CRYPTO"]
    loop = asyncio.get_event_loop()
    n = 20

    results = {}

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1, initializer=process_pool_init
    ) as pool:
        # Warmup: the initializer runs once per worker process, not per call
        print("     Warming up process pool worker (initializer runs once)...")
        await loop.run_in_executor(pool, process_pool_scan, text_500, entities)
        print("     Worker warmed up.")

        for text, label in [(text_300, "300_chars"), (text_500, "500_chars"), (text_1000, "1000_chars")]:
            timings = []
            for _ in range(n):
                t0 = time.perf_counter()
                await loop.run_in_executor(pool, process_pool_scan, text, entities)
                timings.append((time.perf_counter() - t0) * 1000.0)

            stats = {
                "p50": percentile(timings, 50),
                "p99": percentile(timings, 99),
                "mean": statistics.mean(timings),
            }
            results[label] = stats
            print(f"     {label}: p50={stats['p50']:.2f}ms  p99={stats['p99']:.2f}ms  mean={stats['mean']:.2f}ms")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# BENCHMARK 2: Regex fast path latency
# ─────────────────────────────────────────────────────────────────────────────

def bench_regex_fast_path() -> dict:
    """Benchmark regex-only scan across input sizes."""
    print("\n[B2] Regex fast path latency benchmark...")

    sizes = [100, 300, 500, 1000, 2000, 4000, 8192]
    results = {}
    n = 100

    for size in sizes:
        text = generate_text(size)
        timings = []
        for _ in range(n):
            t = measure_ms(regex_scan, text)
            timings.append(t)

        stats = {
            "p50": percentile(timings, 50),
            "p99": percentile(timings, 99),
            "mean": statistics.mean(timings),
            "min": min(timings),
            "max": max(timings),
        }
        results[size] = stats
        print(f"     {size:5d} chars: p50={stats['p50']:.3f}ms  p99={stats['p99']:.3f}ms  mean={stats['mean']:.3f}ms")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# BENCHMARK 3: Short typical prompts (100-400 chars) — most common LLM usage
# ─────────────────────────────────────────────────────────────────────────────

def bench_typical_prompts(analyzer) -> dict:
    """Benchmark on short typical prompts (the most common LLM API usage)."""
    print("\n[B3] Typical prompt size benchmark (100-400 chars, minimal entity set)...")

    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    entities = ["CREDIT_CARD", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CRYPTO"]
    sizes = [100, 150, 200, 250, 300, 350, 400]
    results = {}
    n = 50

    for size in sizes:
        text = generate_text(size)
        # Warmup
        for _ in range(3):
            analyzer.analyze(text=text, language="en", entities=entities)

        timings = []
        for _ in range(n):
            t = measure_ms(analyzer.analyze, text=text, language="en", entities=entities)
            timings.append(t)

        stats = {
            "p50": percentile(timings, 50),
            "p95": percentile(timings, 95),
            "p99": percentile(timings, 99),
            "mean": statistics.mean(timings),
        }
        results[size] = stats
        flag = ""
        if stats["p99"] > 40:
            flag = " ← WOULD EXCEED 50ms budget (scan > 40ms)"
        elif stats["p99"] > 30:
            flag = " ← tight (scan ~30-40ms, leaves 10-20ms for HTTP overhead)"
        print(f"     {size:3d} chars: p50={stats['p50']:.2f}ms  p99={stats['p99']:.2f}ms{flag}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# BENCHMARK 4: Recommended architecture simulation
# The architecture: regex sync gate (always) + Presidio async with timeout
# ─────────────────────────────────────────────────────────────────────────────

async def bench_recommended_architecture(analyzer) -> dict:
    """
    Simulate the recommended 2-tier architecture:
    1. Regex sync fast path: run synchronously, gate on results
    2. Presidio async: run in thread pool, but we'll add a 40ms timeout
       If Presidio completes in time, use results. If not, forward anyway (regex passed).
    """
    print("\n[B4] Recommended architecture simulation (regex + async Presidio with timeout)...")

    entities = ["CREDIT_CARD", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CRYPTO"]
    loop = asyncio.get_event_loop()
    sizes = [300, 500, 1000, 2000]
    results = {}
    n = 30

    PRESIDIO_TIMEOUT = 0.040  # 40ms timeout for Presidio in the sync path

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as tpool:
        for size in sizes:
            text = generate_text(size)
            # Warmup
            for _ in range(3):
                await loop.run_in_executor(tpool, analyzer.analyze, text, "en", entities)

            timings_full = []  # time to complete both regex + presidio
            timings_regex_only = []  # time for regex alone
            presidio_timeouts = 0

            for _ in range(n):
                t0 = time.perf_counter()

                # Step 1: Regex fast path (always sync)
                regex_results = regex_scan(text)
                regex_done_ms = (time.perf_counter() - t0) * 1000.0
                timings_regex_only.append(regex_done_ms)

                # Step 2: Presidio async with timeout
                try:
                    presidio_future = loop.run_in_executor(
                        tpool, analyzer.analyze, text, "en", entities
                    )
                    await asyncio.wait_for(presidio_future, timeout=PRESIDIO_TIMEOUT)
                    # Presidio completed in time
                except asyncio.TimeoutError:
                    presidio_timeouts += 1
                    # Presidio timed out — in production, we'd continue (regex passed)

                total_ms = (time.perf_counter() - t0) * 1000.0
                timings_full.append(min(total_ms, PRESIDIO_TIMEOUT * 1000 + regex_done_ms + 1))
                # Cap at timeout + regex overhead (this is what the user would wait)

            stats = {
                "regex_p50": percentile(timings_regex_only, 50),
                "regex_p99": percentile(timings_regex_only, 99),
                "full_p50": percentile(timings_full, 50),
                "full_p99": percentile(timings_full, 99),
                "presidio_timeout_rate": presidio_timeouts / n,
                "presidio_timeouts": presidio_timeouts,
                "n": n,
            }
            results[size] = stats
            print(f"     {size:5d} chars: regex_p99={stats['regex_p99']:.2f}ms  "
                  f"full_p99={stats['full_p99']:.2f}ms  "
                  f"presidio_timeout_rate={stats['presidio_timeout_rate']*100:.0f}%")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# BENCHMARK 5: Latency scaling model
# ─────────────────────────────────────────────────────────────────────────────

def bench_latency_scaling(analyzer) -> dict:
    """Generate a detailed latency-vs-input-size scaling model."""
    print("\n[B5] Latency scaling model (custom_minimal entity set)...")

    entities = ["CREDIT_CARD", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CRYPTO"]
    # Fine-grained sizes
    sizes = [50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 900, 1000]
    results = {}
    n = 30

    for size in sizes:
        text = generate_text(size)
        for _ in range(3):
            analyzer.analyze(text=text, language="en", entities=entities)

        timings = []
        for _ in range(n):
            t = measure_ms(analyzer.analyze, text=text, language="en", entities=entities)
            timings.append(t)

        stats = {
            "p50": percentile(timings, 50),
            "p99": percentile(timings, 99),
            "mean": statistics.mean(timings),
        }
        results[size] = stats
        flag = "✅" if stats["p99"] < 40 else "❌"
        print(f"     {flag} {size:4d} chars: p50={stats['p50']:.2f}ms  p99={stats['p99']:.2f}ms")

    # Find the threshold where p99 exceeds 40ms (budget limit for scan component)
    threshold_chars = None
    for size in sorted(sizes):
        if results[size]["p99"] >= 40:
            threshold_chars = size
            break

    results["threshold_40ms_p99"] = threshold_chars
    print(f"\n     ⚡ p99 exceeds 40ms at: {threshold_chars} chars (custom_minimal entity set)")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 70)
    print("OnGarde Presidio Benchmark Suite — Part 2")
    print("=" * 70)
    print(f"Python: {sys.version}")

    all_results = {}

    # Create analyzer
    print("\nInitializing warm analyzer...")
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    }
    provider = NlpEngineProvider(nlp_configuration=configuration)
    nlp_engine = provider.create_engine()
    analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
    # Warm up
    for _ in range(5):
        analyzer.analyze(text=generate_text(500), language="en",
                        entities=["CREDIT_CARD", "EMAIL_ADDRESS"])
    print("Analyzer warmed up.")

    # ── B1: Proper process pool ──────────────────────────────────────────────
    all_results["proper_process_pool"] = await bench_proper_process_pool()

    # ── B2: Regex fast path ──────────────────────────────────────────────────
    all_results["regex_fast_path"] = bench_regex_fast_path()

    # ── B3: Typical prompts ──────────────────────────────────────────────────
    all_results["typical_prompts"] = bench_typical_prompts(analyzer)

    # ── B4: Recommended architecture ────────────────────────────────────────
    all_results["recommended_arch"] = await bench_recommended_architecture(analyzer)

    # ── B5: Latency scaling ──────────────────────────────────────────────────
    all_results["latency_scaling"] = bench_latency_scaling(analyzer)

    # Save
    output_path = "/root/.openclaw/workspace/ongarde/benchmarks/results2.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n✓ Results saved to {output_path}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print("\nProper ProcessPoolExecutor (with initializer, model pre-loaded):")
    for label, s in all_results["proper_process_pool"].items():
        print(f"  {label}: p50={s['p50']:.2f}ms  p99={s['p99']:.2f}ms")

    print("\nRegex fast path (all sizes):")
    for size, s in all_results["regex_fast_path"].items():
        print(f"  {size:5d} chars: p50={s['p50']:.3f}ms  p99={s['p99']:.3f}ms")

    print("\nTypical prompts (100-400 chars, custom_minimal entity set):")
    for size, s in all_results["typical_prompts"].items():
        flag = "✅" if s["p99"] < 30 else ("⚠️" if s["p99"] < 40 else "❌")
        print(f"  {flag} {size:3d} chars: p50={s['p50']:.2f}ms  p99={s['p99']:.2f}ms")

    print(f"\nLatency threshold: p99 > 40ms at {all_results['latency_scaling'].get('threshold_40ms_p99', 'N/A')} chars")

    print("\nRecommended architecture (regex sync + Presidio async 40ms timeout):")
    for size, s in all_results["recommended_arch"].items():
        print(f"  {size:5d} chars: regex_p99={s['regex_p99']:.2f}ms  "
              f"full_p99={s['full_p99']:.2f}ms  "
              f"presidio_timeout_rate={s['presidio_timeout_rate']*100:.0f}%")

    return all_results


if __name__ == "__main__":
    asyncio.run(main())
