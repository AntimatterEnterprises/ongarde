"""Unit tests for E-001-S-001: FastAPI application factory + lifespan lifecycle.

Covers all ACs defined in the story:
  AC-E001-09 (partial): 503 before ready, 200 after ready, correct 200 body
  AC-E001-08 (partial): startup failure on bad config (SystemExit, non-zero)
  Additional:           create_app() importable, lifespan order, shutdown
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.config import Config, load_config
from app.main import create_app, lifespan

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _stub_config() -> Config:
    """Return a default Config for testing (no file I/O)."""
    return Config.defaults()


def _patch_load_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch load_config in app.main to return a stub Config."""
    monkeypatch.setattr("app.main.load_config", lambda: _stub_config())


# ─── AC-5: create_app() importable without side effects ───────────────────────


class TestCreateAppFactory:
    """AC-5: The factory function is importable and testable in isolation."""

    def test_create_app_returns_fastapi_instance(self) -> None:
        """create_app() returns a FastAPI application."""
        application = create_app()
        assert isinstance(application, FastAPI)

    def test_create_app_multiple_calls_return_independent_instances(self) -> None:
        """Each call to create_app() returns a distinct, independent instance."""
        app1 = create_app()
        app2 = create_app()
        assert app1 is not app2

    def test_create_app_initialises_ready_false(self) -> None:
        """app.state.ready is False immediately after create_app() — before lifespan."""
        application = create_app()
        assert application.state.ready is False

    def test_create_app_no_startup_side_effects(self) -> None:
        """create_app() does not start uvicorn or run the lifespan."""
        # Just importing and calling create_app() should not raise or start servers.
        for _ in range(3):
            application = create_app()
            assert isinstance(application, FastAPI)


# ─── AC-1: HTTP 503 before ready ──────────────────────────────────────────────


class TestHealth503BeforeReady:
    """AC-E001-09 (partial): /health returns 503 before app.state.ready is True."""

    @pytest.mark.asyncio
    async def test_health_returns_503_before_ready(self) -> None:
        """/health returns 503 when app.state.ready is False (pre-startup)."""
        application = create_app()
        # ASGITransport sends HTTP requests without triggering the ASGI lifespan.
        # app.state.ready remains False (set by create_app()).
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_health_503_body_contains_status_starting(self) -> None:
        """/health 503 body contains status: starting."""
        application = create_app()
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 503
        body = response.json()
        # Our custom exception handler wraps HTTPException.detail in {"error": ...}
        error = body.get("error", {})
        assert isinstance(error, dict), f"Expected dict in error field, got: {body}"
        assert error.get("status") == "starting"
        assert error.get("scanner") == "initializing"

    @pytest.mark.asyncio
    async def test_health_503_when_ready_explicitly_false(self) -> None:
        """/health returns 503 when app.state.ready is explicitly set to False."""
        application = create_app()
        application.state.ready = False
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_proxy_routes_return_503_before_ready(self) -> None:
        """Proxy routes enforced by require_ready dependency also return 503."""
        application = create_app()
        transport = ASGITransport(app=application)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            )
        # require_ready dependency raises 503
        assert response.status_code == 503


# ─── AC-2, AC-3: HTTP 200 after ready ─────────────────────────────────────────


class TestHealth200AfterReady:
    """AC-E001-09 (partial): /health returns 200 with correct body after startup."""

    def test_health_200_after_lifespan_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/health returns 200 after the lifespan startup sequence completes."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as client:
            response = client.get("/health")

        assert response.status_code == 200

    def test_health_200_body_has_proxy_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3: /health 200 body contains 'proxy' field."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as client:
            body = client.get("/health").json()

        assert "proxy" in body, f"'proxy' field missing from: {body}"
        assert body["proxy"] == "running"

    def test_health_200_body_has_scanner_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3: /health 200 body contains 'scanner' field."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as client:
            body = client.get("/health").json()

        assert "scanner" in body, f"'scanner' field missing from: {body}"

    def test_health_200_body_has_scanner_mode_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3: /health 200 body contains 'scanner_mode' field."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as client:
            body = client.get("/health").json()

        assert "scanner_mode" in body, f"'scanner_mode' field missing from: {body}"
        assert body["scanner_mode"] == "full"  # default config

    def test_health_200_body_all_required_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3: /health 200 body includes ALL required fields."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as client:
            body = client.get("/health").json()

        required_fields = {"proxy", "scanner", "scanner_mode"}
        missing = required_fields - set(body.keys())
        assert not missing, f"Missing required fields in /health response: {missing}"


# ─── AC-6: Startup sequence order ─────────────────────────────────────────────


class TestLifespanStartupSequence:
    """AC-E001 additional: lifespan startup sequence and state management."""

    def test_ready_false_before_lifespan(self) -> None:
        """app.state.ready is False before lifespan runs."""
        application = create_app()
        assert application.state.ready is False

    def test_ready_true_after_lifespan_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-6: app.state.ready is True after startup completes."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as _client:
            # During the with-block, lifespan has completed startup
            assert application.state.ready is True

    def test_config_stored_in_app_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-6 Step 1: config loaded and stored in app.state.config."""
        stub = _stub_config()
        monkeypatch.setattr("app.main.load_config", lambda: stub)
        application = create_app()

        with TestClient(application) as _client:
            assert hasattr(application.state, "config")
            assert isinstance(application.state.config, Config)

    def test_audit_backend_stored_in_app_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-6 Step 2: audit backend created and stored in app.state.audit_backend."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as _client:
            assert hasattr(application.state, "audit_backend")
            # E-005: real backend (LocalSQLiteBackend or SupabaseBackend) replaces NullAuditBackend stub
            from app.audit.protocol import AuditBackend
            assert isinstance(application.state.audit_backend, AuditBackend)

    def test_scan_pool_stored_in_app_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-6 Step 3: scan pool is stored in app.state.scan_pool (E-003: real pool or None on failure)."""
        from concurrent.futures import ProcessPoolExecutor

        from app.scanner.calibration import CalibrationResult
        _patch_load_config(monkeypatch)
        # Mock startup_scan_pool to avoid spawning real processes in unit tests
        monkeypatch.setattr(
            "app.main.startup_scan_pool",
            AsyncMock(return_value=(None, CalibrationResult.conservative_fallback("mocked")))
        )
        application = create_app()

        with TestClient(application) as _client:
            assert hasattr(application.state, "scan_pool")
            # Pool may be None (when mocked) or a ProcessPoolExecutor (real)
            assert application.state.scan_pool is None or isinstance(
                application.state.scan_pool, ProcessPoolExecutor
            )


# ─── AC-7: Shutdown sequence ──────────────────────────────────────────────────


class TestLifespanShutdownSequence:
    """AC-E001 additional: lifespan shutdown sequence."""

    def test_ready_false_after_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-7: app.state.ready is False after shutdown."""
        _patch_load_config(monkeypatch)
        application = create_app()

        with TestClient(application) as _client:
            assert application.state.ready is True

        # After TestClient exits, lifespan shutdown has run
        assert application.state.ready is False

    def test_shutdown_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-7: Shutdown completes without exceptions."""
        _patch_load_config(monkeypatch)
        application = create_app()

        # Should not raise any exception during startup or shutdown
        with TestClient(application) as _client:
            pass  # Just start and stop


# ─── AC-4: Config error causes non-zero exit ──────────────────────────────────


class TestConfigLoadingFailures:
    """AC-E001-08 (partial): startup fails fast on config errors."""

    def test_load_config_raises_system_exit_on_missing_version(
        self, tmp_path: Any
    ) -> None:
        """AC-4: load_config raises SystemExit when 'version' field is missing."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "# Config without version field\n"
            "upstream:\n"
            "  openai: 'https://api.openai.com'\n"
        )

        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))

        assert exc_info.value.code == 1, (
            f"Expected SystemExit with code 1, got code: {exc_info.value.code}"
        )

    def test_load_config_raises_system_exit_on_wrong_version(
        self, tmp_path: Any
    ) -> None:
        """AC-4: load_config raises SystemExit when version is unsupported."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 99\n")

        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))

        assert exc_info.value.code == 1

    def test_load_config_raises_system_exit_on_invalid_yaml(
        self, tmp_path: Any
    ) -> None:
        """AC-4: load_config raises SystemExit on YAML parse error."""
        config_file = tmp_path / "config.yaml"
        # Write deliberately broken YAML
        config_file.write_text("version: 1\n  invalid_indent:\nbroken: [unclosed\n")

        with pytest.raises(SystemExit) as exc_info:
            load_config(config_path=str(config_file))

        assert exc_info.value.code == 1

    def test_load_config_returns_defaults_when_file_not_found(self) -> None:
        """load_config returns defaults (no error) when file does not exist."""
        config = load_config(config_path="/nonexistent/path/config.yaml")
        assert isinstance(config, Config)
        assert config.version == 1
        assert config.scanner.mode == "full"

    def test_load_config_succeeds_with_valid_version(
        self, tmp_path: Any
    ) -> None:
        """load_config succeeds with a minimal valid config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 1\n")

        config = load_config(config_path=str(config_file))

        assert isinstance(config, Config)
        assert config.version == 1

    @pytest.mark.asyncio
    async def test_lifespan_does_not_set_ready_when_config_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-4: lifespan never sets app.state.ready=True when config loading fails."""

        def bad_load_config() -> Config:
            raise SystemExit(1)

        monkeypatch.setattr("app.main.load_config", bad_load_config)
        application = create_app()

        # Run the lifespan directly (without the TestClient thread-pool overhead).
        # SystemExit should propagate out of the async context manager.
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(application):
                pass  # Should not reach here

        assert exc_info.value.code == 1
        # The critical invariant: ready was NEVER set to True
        assert application.state.ready is False


# ─── Integration: full request cycle after startup ────────────────────────────


class TestHealthEndpointIntegration:
    """Integration tests verifying /health behaves correctly across the lifecycle."""

    def test_health_transitions_from_503_to_200(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify 503 pre-startup and 200 post-startup on the same app instance."""
        _patch_load_config(monkeypatch)
        application = create_app()

        # Pre-startup: should be 503 (via async client, no lifespan)
        import asyncio

        from httpx import ASGITransport, AsyncClient

        async def check_503() -> int:
            transport = ASGITransport(app=application)  # type: ignore[arg-type]
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                return (await c.get("/health")).status_code

        pre_startup_status = asyncio.get_event_loop().run_until_complete(check_503())
        assert pre_startup_status == 503, (
            f"Expected 503 pre-startup, got {pre_startup_status}"
        )

        # Post-startup: should be 200
        with TestClient(application) as client:
            post_startup_status = client.get("/health").status_code

        assert post_startup_status == 200, (
            f"Expected 200 post-startup, got {post_startup_status}"
        )
