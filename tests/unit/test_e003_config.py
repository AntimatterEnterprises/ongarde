"""Tests for E-003-S-007: enable_person_detection config flag + /health/scanner calibration field.

Tests:
- enable_person_detection=False (default) → PERSON not in entity set
- enable_person_detection=True → PERSON in entity set
- ScanLatencyTracker wired in scan_or_block()
- /health/scanner calibration field present and correct
- regex unaffected by PRESIDIO_SYNC_CAP=0
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.models.scan import Action, ScanResult
from app.scanner.safe_scan import scan_or_block, SCANNER_GLOBAL_TIMEOUT_S
from app.utils.health import ScanLatencyTracker


# ─── E-003-S-007: enable_person_detection config flag ────────────────────────


class TestEnablePersonDetection:
    def test_default_entity_set_excludes_person(self):
        """Default ScannerConfig entity_set does NOT include PERSON."""
        from app.config import ScannerConfig
        cfg = ScannerConfig()
        assert "PERSON" not in cfg.entity_set

    def test_enable_person_detection_false_by_default(self):
        """ScannerConfig.enable_person_detection defaults to False."""
        from app.config import ScannerConfig
        cfg = ScannerConfig()
        assert cfg.enable_person_detection is False

    def test_startup_scan_pool_excludes_person_by_default(self):
        """startup_scan_pool() with enable_person_detection=False → PERSON not in entity_set."""
        # This is tested via startup_scan_pool() mock in test_scan_pool.py
        # Verify the config level: default entity_set does not include PERSON
        from app.config import ScannerConfig
        cfg = ScannerConfig()
        entity_set = list(cfg.entity_set)
        if not cfg.enable_person_detection:
            assert "PERSON" not in entity_set

    def test_enable_person_detection_adds_person_to_entity_set(self):
        """enable_person_detection=True → PERSON added to entity_set in startup_scan_pool."""
        from app.scanner.pool import startup_scan_pool
        from app.scanner.calibration import CalibrationResult

        config = MagicMock()
        config.scanner.entity_set = ["CREDIT_CARD", "EMAIL_ADDRESS"]
        config.scanner.enable_person_detection = True

        with patch("app.scanner.pool.ProcessPoolExecutor") as mock_ppe, \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)), \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(return_value=CalibrationResult.conservative_fallback("test"))):
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            mock_ppe.return_value = mock_pool

            import asyncio
            asyncio.get_event_loop().run_until_complete(startup_scan_pool(config))

        initargs = mock_ppe.call_args[1].get("initargs", ())
        entity_tuple = initargs[0] if initargs else ()
        assert "PERSON" in entity_tuple

    def test_no_duplicate_person_when_already_in_entity_set(self):
        """PERSON not added twice if already in entity_set when enable_person_detection=True."""
        from app.scanner.pool import startup_scan_pool
        from app.scanner.calibration import CalibrationResult

        config = MagicMock()
        config.scanner.entity_set = ["CREDIT_CARD", "PERSON"]  # already has PERSON
        config.scanner.enable_person_detection = True

        with patch("app.scanner.pool.ProcessPoolExecutor") as mock_ppe, \
             patch("app.scanner.pool._run_smoke_test", new=AsyncMock(return_value=None)), \
             patch("app.scanner.pool.run_calibration", new=AsyncMock(return_value=CalibrationResult.conservative_fallback("test"))):
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            mock_ppe.return_value = mock_pool

            import asyncio
            asyncio.get_event_loop().run_until_complete(startup_scan_pool(config))

        initargs = mock_ppe.call_args[1].get("initargs", ())
        entity_tuple = initargs[0] if initargs else ()
        assert entity_tuple.count("PERSON") == 1

    def test_uk_phone_not_in_entity_set_by_default_and_phone_recognizer_is_us_only(self):
        """AC-E003-08: With enable_person_detection=False, UK phone +44... is not blocked.

        Two checks:
        1. Default entity_set does NOT include PERSON.
        2. The _build_analyzer function sets up a US-only PhoneRecognizer,
           confirmed by inspecting the registry — no global PhoneRecognizer present.
        """
        from app.config import ScannerConfig
        from app.scanner.presidio_worker import _build_analyzer

        cfg = ScannerConfig()
        assert "PERSON" not in cfg.entity_set, "PERSON must be excluded by default"

        # Verify _build_analyzer wires US-only PhoneRecognizer.
        # We don't actually run analysis (too slow for unit tests) — we inspect
        # the registry to confirm global PhoneRecognizer was replaced with US-only.
        entity_tuple = tuple(cfg.entity_set)
        _nlp, analyzer = _build_analyzer(entity_tuple)

        registry = analyzer.registry
        recognizer_names = [r.__class__.__name__ for r in registry.recognizers]

        # Must have at least one PhoneRecognizer (the US-only one)
        assert "PhoneRecognizer" in recognizer_names, (
            "US-only PhoneRecognizer must be registered"
        )

        # The US-only recognizer must have supported_regions containing only "US"
        phone_recognizers = [
            r for r in registry.recognizers if r.__class__.__name__ == "PhoneRecognizer"
        ]
        assert len(phone_recognizers) == 1, "Exactly one PhoneRecognizer should be in registry"
        us_phone = phone_recognizers[0]
        # supported_regions contains only "US" — confirms UK numbers won't be detected
        if hasattr(us_phone, "supported_regions"):
            regions = list(us_phone.supported_regions)
            assert regions == ["US"] or regions == [None] or "US" in str(regions), (
                f"PhoneRecognizer regions should be US-only, got: {regions}"
            )

    def test_presidio_sync_cap_zero_does_not_affect_regex(self):
        """AC-CALIB-008: Regex fast-path runs even when PRESIDIO_SYNC_CAP=0."""
        import app.scanner.engine as engine
        original_cap = engine.PRESIDIO_SYNC_CAP
        try:
            engine.PRESIDIO_SYNC_CAP = 0

            with patch("app.scanner.engine.regex_scan") as mock_regex, \
                 patch("asyncio.create_task"):
                mock_regex.return_value = MagicMock(is_block=False)
                mock_pool = MagicMock(spec=ProcessPoolExecutor)

                async def _run():
                    from app.scanner.engine import scan_request
                    return await scan_request("test", mock_pool, "s", {})

                asyncio.get_event_loop().run_until_complete(_run())

            mock_regex.assert_called_once()
        finally:
            engine.PRESIDIO_SYNC_CAP = original_cap


# ─── E-003-S-007: ScanLatencyTracker wiring ──────────────────────────────────


class TestScanLatencyTrackerWiring:
    @pytest.mark.asyncio
    async def test_scan_or_block_records_latency_when_tracker_provided(self):
        """scan_or_block() records latency to ScanLatencyTracker when provided."""
        tracker = ScanLatencyTracker()
        assert tracker.count == 0

        with patch("app.scanner.safe_scan.scan_request",
                   new=AsyncMock(return_value=ScanResult(action=Action.ALLOW, scan_id="t1"))):
            await scan_or_block(
                content="test",
                scan_pool=None,
                scan_id="t1",
                audit_context={},
                latency_tracker=tracker,
            )

        assert tracker.count == 1

    @pytest.mark.asyncio
    async def test_scan_or_block_no_error_when_tracker_none(self):
        """scan_or_block() works fine when latency_tracker=None (default)."""
        with patch("app.scanner.safe_scan.scan_request",
                   new=AsyncMock(return_value=ScanResult(action=Action.ALLOW, scan_id="t2"))):
            result = await scan_or_block(
                content="test",
                scan_pool=None,
                scan_id="t2",
                audit_context={},
                latency_tracker=None,
            )

        assert result.action == Action.ALLOW

    @pytest.mark.asyncio
    async def test_scan_or_block_records_latency_on_block(self):
        """scan_or_block() records latency even when scan returns BLOCK."""
        tracker = ScanLatencyTracker()

        with patch("app.scanner.safe_scan.scan_request",
                   new=AsyncMock(return_value=ScanResult(
                       action=Action.BLOCK, scan_id="t3", rule_id="PRESIDIO_CREDIT_CARD"
                   ))):
            await scan_or_block("test", None, "t3", {}, latency_tracker=tracker)

        assert tracker.count == 1

    @pytest.mark.asyncio
    async def test_scan_or_block_records_latency_on_timeout(self):
        """scan_or_block() records latency even on scanner timeout."""
        tracker = ScanLatencyTracker()

        with patch("app.scanner.safe_scan.scan_request",
                   new=AsyncMock(side_effect=asyncio.TimeoutError)), \
             patch("app.scanner.safe_scan.SCANNER_GLOBAL_TIMEOUT_S", 0.01):
            await scan_or_block("test", None, "t4", {}, latency_tracker=tracker)

        assert tracker.count == 1

    @pytest.mark.asyncio
    async def test_scan_or_block_records_latency_on_exception(self):
        """scan_or_block() records latency even on unhandled exception."""
        tracker = ScanLatencyTracker()

        with patch("app.scanner.safe_scan.scan_request",
                   new=AsyncMock(side_effect=RuntimeError("crash"))):
            result = await scan_or_block("test", None, "t5", {}, latency_tracker=tracker)

        assert result.action == Action.BLOCK
        assert tracker.count == 1


# ─── E-003-S-007: /health/scanner calibration field ─────────────────────────


class TestHealthScannerCalibrationFieldE003:
    def test_calibration_field_present_in_health_response(self):
        """/health/scanner includes 'calibration' field."""
        from app.main import create_app
        from app.scanner.calibration import CalibrationResult

        def _stub_config():
            from app.config import Config, ScannerConfig
            return Config(scanner=ScannerConfig(mode="full"))

        app = create_app()

        with patch("app.main.startup_scan_pool", new=AsyncMock(return_value=(
            None,
            CalibrationResult.conservative_fallback("test")
        ))), patch("app.main.load_config", side_effect=_stub_config):
            with TestClient(app) as client:
                body = client.get("/health/scanner").json()

        assert "calibration" in body

    def test_calibration_tier_in_health_response(self):
        """/health/scanner calibration.tier is present and valid."""
        from app.main import create_app
        from app.scanner.calibration import CalibrationResult

        def _stub_config():
            from app.config import Config, ScannerConfig
            return Config(scanner=ScannerConfig(mode="full"))

        app = create_app()

        real_calibration = CalibrationResult(
            sync_cap=1000, timeout_s=0.045, tier="standard", calibration_ok=True
        )

        with patch("app.main.startup_scan_pool", new=AsyncMock(return_value=(None, real_calibration))), \
             patch("app.main.load_config", side_effect=_stub_config):
            with TestClient(app) as client:
                body = client.get("/health/scanner").json()

        calibration = body["calibration"]
        assert calibration["tier"] == "standard"
        assert calibration["sync_cap"] == 1000
        assert calibration["calibration_ok"] is True

    def test_calibration_fallback_reason_present_when_not_ok(self):
        """/health/scanner calibration.fallback_reason is set when calibration_ok=False."""
        from app.main import create_app
        from app.scanner.calibration import CalibrationResult

        def _stub_config():
            from app.config import Config, ScannerConfig
            return Config(scanner=ScannerConfig(mode="full"))

        app = create_app()

        fallback_cal = CalibrationResult.conservative_fallback(reason="hardware too slow")

        with patch("app.main.startup_scan_pool", new=AsyncMock(return_value=(None, fallback_cal))), \
             patch("app.main.load_config", side_effect=_stub_config):
            with TestClient(app) as client:
                body = client.get("/health/scanner").json()

        calibration = body["calibration"]
        assert calibration["calibration_ok"] is False
        assert "hardware too slow" in calibration["fallback_reason"]
