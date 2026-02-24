"""OnGarde FastAPI application factory + lifespan lifecycle.

This module implements:
  - create_app() — testable application factory (story E-001-S-001)
  - lifespan — @asynccontextmanager startup/shutdown sequence
  - /health router — delegated to app/health.py (extended in E-001-S-007)
  - /        route  — service discovery root (inline, not health-critical)
  - app = create_app() — module-level instance for uvicorn

Startup sequence (architecture.md §3.4):
  1. load_config()           → app.state.config
  2. create_audit_backend()  → app.state.audit_backend
  3. startup_scan_pool()     → app.state.scan_pool + app.state.calibration
                               (pool=None stub in E-001; real E-003 adds calibration)
                               Returns (pool, CalibrationResult) — Adaptive Performance
                               Protocol (HOLD-002 fix). Config overrides win.
  3b. create_http_client()   → app.state.http_client (E-001-S-002)
  3c. ScanLatencyTracker()   → app.state.latency_tracker (E-001-S-007)
  4. Config file watcher     → no-op stub (real: E-009)
  5. Retention pruner        → no-op stub (real: E-005)
  6. app.state.ready = True  → log "OnGarde ready. En Garde. 🤺"

Shutdown sequence (reverse):
  app.state.ready = False → cancel pruner → stop watcher →
  shutdown scan pool → close audit backend

Uvicorn hardened defaults (architecture.md §9.3):
  uvicorn app.main:app \\
    --host 127.0.0.1 \\
    --port 4242 \\
    --limit-concurrency 100 \\
    --backlog 50 \\
    --timeout-keep-alive 5 \\
    --timeout-graceful-shutdown 30
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx
import pathlib

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRouter

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.audit.factory import create_audit_backend
from app.audit.protocol import AuditBackend
from app.auth.limiter import limiter
from app.auth.router import router as auth_router
from app.config import Config, load_config
from app.dashboard.api import router as dashboard_router
from app.dashboard.middleware import DashboardLocalhostMiddleware
from app.health import router as health_router
from app.proxy.engine import create_http_client, router as engine_router, shutdown_proxy_engine
from app.proxy.middleware import BodySizeLimitMiddleware
from app.scanner.calibration import CalibrationResult
from app.scanner.pool import shutdown_scan_pool, startup_scan_pool
from app.utils.health import ScanLatencyTracker, StreamingMetricsTracker
from app.utils.logger import configure_logging, get_logger

# ─── Logging Setup ────────────────────────────────────────────────────────────
# Configure logging at module import time (before any other imports that may log).
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO" if not DEBUG else "DEBUG")
JSON_LOGS = os.getenv("JSON_LOGS", "true").lower() == "true"

configure_logging(log_level=LOG_LEVEL, json_output=JSON_LOGS)
logger = get_logger(__name__)


# ─── Routers ──────────────────────────────────────────────────────────────────

# health_router: imported from app.health (extended in E-001-S-007)
# engine_router: imported from app.proxy.engine (catch-all /{path:path})
# root_router:   service-discovery root endpoint (defined below)
root_router = APIRouter(tags=["root"])


# ─── Dependencies ─────────────────────────────────────────────────────────────


async def require_ready(request: Request) -> None:
    """FastAPI dependency: raises HTTP 503 if app.state.ready is not True.

    All proxy routes must consume this dependency.
    The /health endpoint handles the 503 case itself (to return a richer body).
    """
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "starting",
                "scanner": "initializing",
                "message": "OnGarde is starting up. Scanner warming up...",
            },
        )


# ─── Root Endpoint ────────────────────────────────────────────────────────────
# /health and /health/scanner are implemented in app/health.py (E-001-S-007).
# This router provides only the service-discovery root (/).


@root_router.get("/")
async def root() -> dict[str, str]:
    """Root endpoint — service identity / discovery."""
    return {
        "service": "OnGarde",
        "tagline": "En Garde — runtime content security for AI agents",
        "docs": "/docs",
        "health": "/health",
        "dashboard": "/dashboard",
    }


# ─── Proxy Routes — implemented in E-001-S-002 (app/proxy/engine.py) ─────────
# The engine_router (catch-all /{path:path}) handles all /v1/* proxy endpoints:
#   POST /v1/chat/completions  — OpenAI chat
#   POST /v1/completions       — OpenAI legacy
#   POST /v1/embeddings        — OpenAI embeddings
#   POST /v1/messages          — Anthropic Messages API
#
# The router is registered in create_app() via:
#   application.include_router(engine_router)
# (no /v1 prefix — path variable captures the full "v1/..." path)


# ─── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — startup and shutdown sequence.

    Architecture §3.4 startup steps (in order):
      1. load_config()
      2. create_audit_backend()
      3. startup_scan_pool()     ← returns (pool, CalibrationResult); stub in E-001
      3b.calibration applied     ← config overrides win over calibration (HOLD-002 fix)
      4. config file watcher     ← no-op stub in E-001 (real: E-009)
      5. retention pruner task   ← no-op stub in E-001 (real: E-005)
      5b.create_http_client()   ← shared httpx client
      5c.ScanLatencyTracker()   ← rolling latency window
      6. app.state.ready = True  ← log "OnGarde ready. En Garde. 🤺"

    Shutdown (reverse order after yield):
      app.state.ready = False → cancel pruner → stop watcher →
      shutdown scan pool → close audit backend
    """
    logger.info("OnGarde starting up...")

    # ── Step 1: Load configuration ────────────────────────────────────────────
    # load_config() raises SystemExit on parse error or missing version field.
    # This ensures the process exits non-zero before ready=True is ever set.
    config: Config = load_config()
    app.state.config = config
    logger.info(
        "Config loaded",
        scanner_mode=config.scanner.mode,
        scanner_entity_set=config.scanner.entity_set,
    )

    # ── Step 2: Initialize audit backend ─────────────────────────────────────
    # NullAuditBackend stub in E-001; real backend (SQLite/Supabase) in E-005.
    audit_backend: AuditBackend = await create_audit_backend(config)
    app.state.audit_backend = audit_backend

    # ── Step 3: Start scan pool + calibration (Adaptive Performance Protocol) ──
    # startup_scan_pool() returns (pool, CalibrationResult).
    # Stub returns (None, conservative_fallback) in E-001.
    # Real E-003 implementation creates ProcessPoolExecutor, warms it up,
    # then runs run_calibration() to measure actual hardware p99 latency
    # and derive hardware-appropriate PRESIDIO_SYNC_CAP + PRESIDIO_TIMEOUT_S.
    #
    # Config overrides: if scanner.presidio_sync_cap or scanner.presidio_timeout_ms
    # are explicitly set in .ongarde/config.yaml, they win over calibration.
    # Detection: compare against DEFAULT_PRESIDIO_SYNC_CAP (the coded default).
    # If a user set a non-default value, the config.py parser will have stored it.
    #
    # Calibration MUST complete before ready=True. Failure uses conservative
    # defaults (sync_cap=500, timeout=60ms) — never crashes startup.
    #
    # All async — no blocking I/O on the event loop.
    from app.constants import DEFAULT_PRESIDIO_SYNC_CAP  # local import avoids circularity

    try:
        scan_pool, calibration = await startup_scan_pool(config)
    except Exception as _exc:  # noqa: BLE001
        logger.warning(
            "Scan pool startup failed — using conservative calibration fallback",
            error=str(_exc),
        )
        from app.scanner.calibration import CalibrationResult as _CR
        scan_pool = None
        calibration = _CR.conservative_fallback(
            reason=f"Pool startup exception: {type(_exc).__name__}: {_exc}"
        )

    # Apply config overrides (explicit user settings win over calibration)
    _sync_cap_overridden = config.scanner.presidio_sync_cap != DEFAULT_PRESIDIO_SYNC_CAP
    _timeout_overridden = config.scanner.presidio_timeout_ms != 40  # 40ms = coded default

    if _sync_cap_overridden:
        logger.info(
            "Presidio sync_cap overridden by config",
            config_value=config.scanner.presidio_sync_cap,
            calibrated_value=calibration.sync_cap,
        )
        calibration = CalibrationResult(
            sync_cap=config.scanner.presidio_sync_cap,
            timeout_s=calibration.timeout_s,  # keep calibrated timeout unless also overridden
            tier=calibration.tier,
            measurements=calibration.measurements,
            calibration_ok=calibration.calibration_ok,
            fallback_reason=calibration.fallback_reason,
        )
    if _timeout_overridden:
        logger.info(
            "Presidio timeout overridden by config",
            config_value_ms=config.scanner.presidio_timeout_ms,
            calibrated_value_ms=round(calibration.timeout_ms, 1),
        )
        calibration = CalibrationResult(
            sync_cap=calibration.sync_cap,
            timeout_s=config.scanner.presidio_timeout_ms / 1000.0,
            tier=calibration.tier,
            measurements=calibration.measurements,
            calibration_ok=calibration.calibration_ok,
            fallback_reason=calibration.fallback_reason,
        )

    app.state.scan_pool = scan_pool
    app.state.calibration = calibration

    # Push effective thresholds into the scan engine module (one-time startup write).
    # This is the bridge between CalibrationResult and scan_request() routing logic.
    # MUST happen before ready=True so no request can race with a stale threshold.
    from app.scanner.engine import update_calibration as _update_engine_calibration
    _update_engine_calibration(calibration.sync_cap, calibration.timeout_s)

    logger.info(
        "Presidio performance calibration",
        tier=calibration.tier,
        sync_cap=calibration.sync_cap,
        timeout_ms=round(calibration.timeout_ms, 1),
        calibration_ok=calibration.calibration_ok,
        fallback_reason=calibration.fallback_reason,
        measurements={k: round(v, 2) for k, v in calibration.measurements.items()},
    )

    # ── Step 4: Initialize allowlist loader + start config file watcher ─────
    # E-009-S-001: AllowlistLoader with watchfiles hot-reload.
    # Preference order:
    #   1. .ongarde/allowlist.yaml  (dedicated allowlist file — preferred)
    #   2. allowlist: section in .ongarde/config.yaml  (fallback)
    # Watcher task fires within 1 second of file save (AC-E009-02).
    # Non-blocking: watcher runs as asyncio.Task, cancelled on shutdown.
    from app.allowlist.loader import AllowlistLoader  # local import (avoid circular)

    allowlist_loader = AllowlistLoader()
    app.state.allowlist_loader = allowlist_loader

    _allowlist_yaml = pathlib.Path(".ongarde/allowlist.yaml")
    _config_path = pathlib.Path(config.path) if config.path else None

    if _allowlist_yaml.exists():
        _watch_path: str | None = str(_allowlist_yaml)
        _loaded_count = allowlist_loader.load(str(_allowlist_yaml))
        logger.info(
            "Allowlist loaded from allowlist.yaml",
            path=str(_allowlist_yaml),
            count=_loaded_count,
        )
    elif _config_path and _config_path.exists():
        _watch_path = str(_config_path)
        _loaded_count = allowlist_loader.load(str(_config_path))
        logger.info(
            "Allowlist loaded from config.yaml",
            path=str(_config_path),
            count=_loaded_count,
        )
    else:
        _watch_path = None
        _loaded_count = 0
        logger.debug("No allowlist config found — allowlist empty (no suppressions)")

    # Start async watcher (non-blocking I/O — watchfiles.awatch is async)
    config_watcher_task: asyncio.Task[None] | None = None
    if _watch_path:
        config_watcher_task = asyncio.create_task(
            allowlist_loader.start_watcher(_watch_path)
        )
        logger.info("Config file watcher started", path=_watch_path)
    else:
        logger.debug("Config file watcher disabled (no allowlist file to watch)")

    # ── Step 5: Retention pruner (stub) ──────────────────────────────────────
    # Real daily async prune task implemented in E-005.
    retention_task: asyncio.Task[None] | None = None
    logger.debug("Retention pruner: no-op stub (real implementation in E-005)")

    # ── Step 5b: Create shared HTTP proxy client (E-001-S-002) ───────────────
    # Single shared httpx.AsyncClient with connection pooling (pool size = 100).
    # NEVER instantiated per-request — stored in app.state.http_client.
    # Pool size matches --limit-concurrency 100 (architecture.md §9.3).
    http_client: httpx.AsyncClient = create_http_client()
    app.state.http_client = http_client
    logger.info(
        "HTTP proxy client created",
        max_connections=100,
        max_keepalive_connections=100,
        keepalive_expiry_s=30,
        timeout_s=30,
    )

    # ── Step 5c: Initialise scan latency tracker (E-001-S-007) ───────────────
    # Rolling window of last 100 scan durations — used by /health and
    # /health/scanner to report avg_scan_ms and p99_scan_ms.
    # The tracker is populated by the scan pipeline in E-002+ (via
    # app.state.latency_tracker.record(duration_ms)).
    app.state.latency_tracker = ScanLatencyTracker()
    logger.debug("Scan latency tracker initialised (window=100)")

    # ── Step 5d: Initialise streaming metrics tracker (E-004-S-005) ─────────
    # Tracks active streaming connections and per-window scan latencies.
    # Used by /health/scanner streaming_active, window_scan_avg_ms, etc.
    app.state.streaming_tracker = StreamingMetricsTracker()
    logger.debug("Streaming metrics tracker initialised")

    # ── Step 6: Mark as ready ─────────────────────────────────────────────────
    app.state.ready = True
    logger.info("OnGarde ready. En Garde. 🤺")

    # ── Server runs here ──────────────────────────────────────────────────────
    yield

    # ── Shutdown (reverse order) ──────────────────────────────────────────────
    logger.info("OnGarde shutting down...")

    # Mark as not ready — refuse new requests
    app.state.ready = False

    # Cancel retention pruner
    if retention_task is not None and not retention_task.done():
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass

    # Stop config file watcher
    if config_watcher_task is not None and not config_watcher_task.done():
        config_watcher_task.cancel()
        try:
            await config_watcher_task
        except asyncio.CancelledError:
            pass

    # Shutdown scan pool
    await shutdown_scan_pool(app.state.scan_pool)

    # Close audit backend
    await audit_backend.close()

    # Close shared HTTP proxy client (E-001-S-002)
    try:
        await http_client.aclose()
        logger.info("HTTP proxy client closed")
    except Exception as exc:
        logger.warning("HTTP proxy client close error (non-fatal)", error=str(exc))

    # Proxy engine hook (no-op in E-001-S-002; exists for extensibility)
    try:
        await shutdown_proxy_engine()
    except Exception as exc:
        logger.warning("Proxy engine shutdown error (non-fatal)", error=str(exc))

    logger.info("OnGarde shutdown complete")


# ─── Application Factory ──────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Create and configure the OnGarde FastAPI application.

    Call this function directly in unit tests to get an isolated app instance:
        app = create_app()

    The module-level `app` is created at import time for uvicorn:
        uvicorn app.main:app --host 127.0.0.1 --port 4242

    Returns:
        Configured FastAPI application with lifespan, routers, and middleware.
    """
    # Disable Swagger UI and ReDoc in production — they expose the full API schema
    # to anyone who can reach the port, aiding enumeration.
    # Re-enable by setting DEBUG=true (local development only).
    _debug = os.getenv("DEBUG", "false").lower() == "true"

    application = FastAPI(
        title="OnGarde Proxy",
        description="En Garde — Runtime content security layer for self-hosted AI agent platforms",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if _debug else None,
        redoc_url="/redoc" if _debug else None,
        openapi_url="/openapi.json" if _debug else None,
    )

    # Initialize ready flag before lifespan — ensures /health returns 503
    # on any request that somehow arrives before startup completes.
    application.state.ready = False

    # Rate limiter (SEC-007) — attached to app state as required by slowapi.
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS middleware — E-009: restricted to localhost origins (AC-E009: CORS fix).
    # The proxy and dashboard are localhost-only services (architecture.md §9.3).
    # allow_origins=["*"] removed — replaced with explicit localhost origins only.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:4242",
            "http://127.0.0.1:4242",
            "http://localhost:3000",   # Dev dashboard (if served separately)
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Request body size limit middleware (E-001-S-005).
    # Enforces MAX_REQUEST_BODY_BYTES (1 MB) hard cap; returns HTTP 413 for oversized
    # bodies BEFORE any scan gate or upstream connection is attempted.
    # NOTE: In Starlette, the LAST-added middleware is OUTERMOST (runs first).
    # BodySizeLimitMiddleware (added second-to-last) runs BEFORE CORSMiddleware.
    # CORS preflight (OPTIONS) passes the size check as a no-op (no request body).
    application.add_middleware(BodySizeLimitMiddleware)

    # Dashboard localhost enforcement (SEC-002).
    # Registered LAST so it runs FIRST — before body reads, scan gates, or auth.
    # Blocks all non-loopback access to /dashboard/* with HTTP 403.
    # Closes the gap where proxy.host: 0.0.0.0 would expose the dashboard.
    application.add_middleware(DashboardLocalhostMiddleware)

    # Rate limiting middleware (SEC-007). Must be added after state.limiter is set.
    application.add_middleware(SlowAPIMiddleware)

    # Register routers
    # root_router:   / (service discovery; registered first for priority)
    application.include_router(root_router)
    # health_router: /health, /health/scanner (from app/health.py — E-001-S-007)
    application.include_router(health_router)
    # auth_router:   /dashboard/api/keys, /dashboard/api/keys/rotate, /dashboard/api/keys/revoke
    # API key management dashboard endpoints (E-006-S-005)
    application.include_router(auth_router, prefix="/dashboard/api")
    # dashboard_router: /dashboard/api/status, /events, /counters, /health, /quota
    # Dashboard data endpoints (E-008-S-008) — unauthenticated (localhost security boundary)
    # MUST be included BEFORE the catch-all engine_router.
    application.include_router(dashboard_router, prefix="/dashboard/api")
    # Dashboard HTML: /dashboard and /dashboard/ serve the single-page app.
    # Uses explicit FileResponse routes because StaticFiles Mount only partially
    # matches /dashboard (without trailing slash), allowing /{path:path} to win.
    # Explicit routes are FULL matches and take priority correctly (E-008-S-001).
    _dashboard_index = pathlib.Path(__file__).parent / "dashboard" / "static" / "index.html"

    @application.get("/dashboard", include_in_schema=False, tags=["dashboard"])
    @application.get("/dashboard/", include_in_schema=False, tags=["dashboard"])
    async def serve_dashboard_html() -> FileResponse:
        """Serve the OnGarde dashboard SPA (single HTML file, all CSS/JS inline)."""
        return FileResponse(str(_dashboard_index), media_type="text/html")

    # engine_router: catch-all /{path:path} — handles all /v1/* proxy endpoints
    # No prefix: path variable captures full "v1/chat/completions" style paths.
    # require_ready is wired as a router-level dependency so every proxied request
    # is gated on app.state.ready=True before the handler runs (E-001-S-003 M-001 cleanup).
    application.include_router(engine_router, dependencies=[Depends(require_ready)])

    # Global exception handlers
    @application.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        logger.warning(
            "HTTP exception",
            status_code=exc.status_code,
            detail=exc.detail,
            path=str(request.url.path),
        )
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error(
            "Unhandled exception",
            error=str(exc),
            error_type=type(exc).__name__,
            path=str(request.url.path),
        )
        return JSONResponse(
            status_code=500, content={"error": "Internal server error"}
        )

    return application


# ─── Module-Level App (for uvicorn) ───────────────────────────────────────────
# `create_app()` is the factory; this instance is used by uvicorn:
#   uvicorn app.main:app --host 127.0.0.1 --port 4242 --limit-concurrency 100 \
#     --backlog 50 --timeout-keep-alive 5 --timeout-graceful-shutdown 30

app = create_app()


# ─── Dev Entrypoint ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # Load config to get binding settings (host and port).
    # This mirrors what the lifespan does at startup; it is safe to call
    # load_config() here because the lifespan will call it again (idempotent).
    # The ONGARDE_PORT env var override is applied by load_config() automatically.
    _startup_config = load_config()
    host = _startup_config.proxy.host
    port = _startup_config.proxy.port

    logger.info("Starting OnGarde (dev mode)", host=host, port=port)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=DEBUG,
        log_level=LOG_LEVEL.lower(),
        limit_concurrency=100,
        backlog=50,
        timeout_keep_alive=5,
    )
