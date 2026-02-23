"""Smoke test for benchmarks/bench_presidio.py — E-003-S-008.

Verifies the benchmark script is importable and has correct structure
without actually executing the benchmark (too slow for unit tests).

AC-S008-03: bench_presidio.py is a standalone Python script runnable as:
    python benchmarks/bench_presidio.py --output benchmarks/presidio_results.json
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_bench_module():
    """Load bench_presidio.py as a module without executing it."""
    bench_path = Path(__file__).parent.parent.parent / "benchmarks" / "bench_presidio.py"
    assert bench_path.exists(), f"Benchmark script not found: {bench_path}"
    spec = importlib.util.spec_from_file_location("bench_presidio", bench_path)
    mod = importlib.util.module_from_spec(spec)
    return mod, spec


class TestBenchPresidioImports:
    """Verify benchmark script structure (AC-S008-03)."""

    def test_bench_presidio_module_loads_without_error(self):
        """Benchmark script creates a module object without errors."""
        mod, spec = _load_bench_module()
        assert mod is not None

    def test_bench_presidio_has_main_function(self):
        """Benchmark script has a main() entry point."""
        # Load module in isolated namespace
        mod, spec = _load_bench_module()
        # Inject into sys.modules temporarily to allow top-level imports
        sys.modules["bench_presidio"] = mod
        try:
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main"), "bench_presidio.py must have a main() function"
            assert callable(mod.main), "main() must be callable"
        finally:
            sys.modules.pop("bench_presidio", None)

    def test_bench_presidio_has_run_benchmark_function(self):
        """Benchmark script has run_benchmark() async function."""
        mod, spec = _load_bench_module()
        sys.modules["bench_presidio"] = mod
        try:
            spec.loader.exec_module(mod)
            assert hasattr(mod, "run_benchmark"), (
                "bench_presidio.py must have a run_benchmark() coroutine"
            )
            import asyncio
            assert asyncio.iscoroutinefunction(mod.run_benchmark), (
                "run_benchmark() must be an async function"
            )
        finally:
            sys.modules.pop("bench_presidio", None)

    def test_bench_presidio_imports_from_production_modules(self):
        """Benchmark uses real production code paths (not standalone Presidio)."""
        mod, spec = _load_bench_module()
        sys.modules["bench_presidio"] = mod
        try:
            spec.loader.exec_module(mod)
            # Verify the script imports the real pipeline components
            # (these are module-level imports that run on exec_module)
            # If startup_scan_pool / scan_request are used, the module will
            # have them accessible
            src = (Path(__file__).parent.parent.parent / "benchmarks" / "bench_presidio.py").read_text()
            assert "startup_scan_pool" in src, (
                "bench_presidio.py must import startup_scan_pool from app.scanner.pool"
            )
            assert "scan_or_block" in src, (
                "bench_presidio.py must import/use scan_or_block from app.scanner.safe_scan"
            )
            assert "CalibrationResult" in src, (
                "bench_presidio.py must use CalibrationResult from app.scanner.calibration"
            )
            assert "derive_thresholds" in src, (
                "bench_presidio.py must test hardware tiers via derive_thresholds"
            )
        finally:
            sys.modules.pop("bench_presidio", None)

    def test_bench_presidio_has_p99_gate_constant(self):
        """Benchmark has a P99 gate that exits 1 if p99(200-char) > 100ms."""
        mod, spec = _load_bench_module()
        sys.modules["bench_presidio"] = mod
        try:
            spec.loader.exec_module(mod)
            assert hasattr(mod, "P99_GATE_MS"), (
                "bench_presidio.py must define P99_GATE_MS constant"
            )
            assert mod.P99_GATE_MS == 100.0, (
                f"P99_GATE_MS should be 100.0ms, got {mod.P99_GATE_MS}"
            )
            assert hasattr(mod, "P99_GATE_SIZE"), (
                "bench_presidio.py must define P99_GATE_SIZE constant"
            )
            assert mod.P99_GATE_SIZE == 200, (
                f"P99_GATE_SIZE should be 200 chars, got {mod.P99_GATE_SIZE}"
            )
        finally:
            sys.modules.pop("bench_presidio", None)

    def test_bench_presidio_has_hardware_tier_simulations(self):
        """Benchmark defines all 4 hardware tier simulations."""
        mod, spec = _load_bench_module()
        sys.modules["bench_presidio"] = mod
        try:
            spec.loader.exec_module(mod)
            assert hasattr(mod, "TIER_SIMULATIONS"), (
                "bench_presidio.py must define TIER_SIMULATIONS dict"
            )
            tiers = set(mod.TIER_SIMULATIONS.keys())
            expected = {"fast", "standard", "slow", "minimal"}
            assert tiers == expected, (
                f"TIER_SIMULATIONS must include all 4 tiers. Got: {tiers}"
            )
        finally:
            sys.modules.pop("bench_presidio", None)

    def test_results_json_exists_and_has_required_fields(self):
        """presidio_results.json exists and has the E-003-S-008 required fields."""
        import json

        results_path = (
            Path(__file__).parent.parent.parent / "benchmarks" / "presidio_results.json"
        )
        if not results_path.exists():
            # If benchmark hasn't been run yet, this is a soft warning not a hard fail
            import pytest
            pytest.skip("presidio_results.json not yet generated — run bench_presidio.py first")

        with open(results_path) as f:
            data = json.load(f)

        # Detect old HOLD-002 format (pre-E-003-S-008) — skip gracefully
        if "run_date" not in data and "meta" in data:
            import pytest
            pytest.skip(
                "presidio_results.json is in old HOLD-002 format — "
                "run bench_presidio.py to generate E-003-S-008 results"
            )

        # Required fields for E-003-S-008 (AC-S008-01, AC-S008-02)
        required_top_level = [
            "run_date",
            "hardware_info",
            "calibration_tier",
            "calibration_sync_cap",
            "calibration_timeout_ms",
            "calibration_ok",
            "first_warm_call_ms",
            "latency_by_size",
            "concurrency_100",
            "advisory_schedule_ms",
            "advisory_nonblocking_verified",
        ]
        for field in required_top_level:
            assert field in data, f"presidio_results.json missing required field: '{field}'"

        # hardware_info must have os, cpu_count, python_version
        hw = data["hardware_info"]
        for hw_field in ["cpu_count", "os", "python_version"]:
            assert hw_field in hw, f"hardware_info missing: '{hw_field}'"

        # calibration_tier must be one of the 4 valid tiers
        assert data["calibration_tier"] in ("fast", "standard", "slow", "minimal", "unknown"), (
            f"calibration_tier must be a valid tier, got: {data['calibration_tier']}"
        )
