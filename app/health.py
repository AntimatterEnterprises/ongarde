"""Health endpoints for OnGarde.

Implements:
  GET /health         — primary health check (503 before ready, 200 after)
  GET /health/scanner — detailed scanner metrics (503 before ready, 200 after)

Both endpoints share the same ``app.state.ready`` gate established in
E-001-S-001 (lifespan startup).  This module *extends* that gate — it does
NOT re-implement it.

/health is polled by:
  - Container/cloud health probes
  - ``npx @ongarde/openclaw start`` (polls until 200, then exits 0)
  - OnGarde dashboard status indicator (Feature 1)

/health/scanner is consumed by:
  - Dashboard Feature 4 (Scanner Health card)
  - Monitoring / alerting pipelines

Story references:
  E-001-S-001 — established ``app.state.ready`` gate and basic /health stub
  E-001-S-007 — THIS STORY: full /health spec + /health/scanner + ScanLatencyTracker
  E-003-S-007 — will replace check_scanner_health() stub with real Presidio metrics
  HOLD-002    — Adaptive Performance Protocol: calibration field in /health/scanner
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from app.config import Config
from app.utils.health import ScanLatencyTracker, StreamingMetricsTracker, check_scanner_health

router = APIRouter(tags=["health"])


# ─── /health ──────────────────────────────────────────────────────────────────


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Primary health check endpoint.

    Returns HTTP 503 before ``app.state.ready`` is set (during lifespan startup).
    Returns HTTP 200 with a full status body once startup completes.

    Response body (200):
        {
          "status": "ok" | "degraded",
          "proxy": "running",
          "scanner": "healthy" | "degraded" | "error",
          "scanner_mode": "full" | "lite",
          "connection_pool_size": 100,
          "avg_scan_ms": 0.0,
          "queue_depth": 0,
          "deployment_mode": "self-hosted" | "managed",
          "audit_path": "/path/to/audit.db" | null
        }

    Response body (503):
        {
          "status": "starting",
          "scanner": "initializing",
          "message": "OnGarde is starting up. Scanner warming up..."
        }

    AC-E001-09: 503 before ready; 200 with ALL required fields after ready.
    M-003 fix (deferred from E-001-S-002 review): ``connection_pool_size`` field added.
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

    config: Config = request.app.state.config
    scan_pool = request.app.state.scan_pool

    # Pull the latency tracker from app.state (set by lifespan in startup).
    # Fall back to a fresh tracker on the off-chance state was not initialised
    # (defensive — should never happen in production).
    latency_tracker: Optional[ScanLatencyTracker] = getattr(
        request.app.state, "latency_tracker", None
    )

    scanner_health = await check_scanner_health(
        scan_pool=scan_pool,
        latency_tracker=latency_tracker,
    )

    # deployment_mode: "self-hosted" when SUPABASE_URL is absent
    is_self_hosted = not bool(os.environ.get("SUPABASE_URL"))

    return {
        "status": "ok" if scanner_health.healthy else "degraded",
        "proxy": "running",
        "scanner": "healthy" if scanner_health.healthy else "error",
        "scanner_mode": config.scanner.mode,
        # connection_pool_size: configured httpx Limits.max_connections (architecture §9.3).
        # Dynamic pool introspection is not part of the public httpx API in a
        # cross-version-compatible way; we report the configured constant (M-003 fix).
        "connection_pool_size": 100,
        "avg_scan_ms": scanner_health.avg_latency_ms,
        "queue_depth": scanner_health.queue_depth,
        "deployment_mode": "self-hosted" if is_self_hosted else "managed",
        "audit_path": (
            os.path.expanduser(config.audit.path) if is_self_hosted else None
        ),
    }


# ─── /health/scanner ──────────────────────────────────────────────────────────


@router.get("/health/scanner")
async def health_scanner(request: Request) -> dict[str, Any]:
    """Detailed scanner health endpoint.

    Returns HTTP 503 before ``app.state.ready`` (same gate as ``/health``).
    Returns HTTP 200 with scanner-specific metrics after startup.

    Response body (200):
        {
          "scanner": "healthy" | "degraded" | "error",
          "scanner_mode": "full" | "lite",
          "entity_set": ["CREDIT_CARD", ...],
          "avg_scan_ms": 0.0,
          "p99_scan_ms": 0.0,
          "queue_depth": 0,
          "pool_workers": 1,
          "calibration": {
            "tier": "fast" | "standard" | "slow" | "minimal",
            "sync_cap": 1000,
            "timeout_ms": 45.0,
            "measured_p99_ms": {"200": 8.1, "500": 18.4, "1000": 29.3},
            "calibration_ok": true,
            "fallback_reason": null
          }
        }

    Fields:
        scanner:      health status (stub: always "healthy")
        scanner_mode: from config.scanner.mode
        entity_set:   from config.scanner.entity_set
        avg_scan_ms:  rolling average of last 100 scans (stub: 0.0)
        p99_scan_ms:  p99 of last 100 scans (stub: 0.0; real: E-003-S-007)
        queue_depth:  pending scan tasks (stub: 0)
        pool_workers: 1 for full mode (1 Presidio worker), 0 for lite / no pool
        calibration:  Adaptive Performance Protocol results (HOLD-002 fix):
                        tier          — hardware tier ("fast"/"standard"/"slow"/"minimal")
                        sync_cap      — effective PRESIDIO_SYNC_CAP chars for this host
                        timeout_ms    — effective PRESIDIO_TIMEOUT_S × 1000
                        measured_p99_ms — raw p99 measurements per size (ms)
                        calibration_ok  — False if conservative fallback was used
                        fallback_reason — human-readable reason (None if calibration_ok)

    AC-E001-09: 503 before ready; 200 with all scanner fields after ready.
    """
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "starting",
                "scanner": "initializing",
            },
        )

    config: Config = request.app.state.config
    scan_pool = request.app.state.scan_pool

    latency_tracker: Optional[ScanLatencyTracker] = getattr(
        request.app.state, "latency_tracker", None
    )

    scanner_health = await check_scanner_health(
        scan_pool=scan_pool,
        latency_tracker=latency_tracker,
    )

    # pool_workers: 1 for full mode (one Presidio ProcessPoolExecutor worker),
    # 0 for lite mode (no worker pool) or when pool not yet initialised.
    pool_workers: int
    if config.scanner.mode == "lite" or scan_pool is None:
        pool_workers = 0
    else:
        pool_workers = 1  # E-003 will introspect pool._max_workers

    # ── Calibration data (Adaptive Performance Protocol — HOLD-002 fix) ──────
    # app.state.calibration is set by lifespan before ready=True.
    # Fall back to None-safe defaults if somehow missing.
    calibration = getattr(request.app.state, "calibration", None)
    if calibration is not None:
        calibration_data: dict[str, Any] = {
            "tier": calibration.tier,
            "sync_cap": calibration.sync_cap,
            "timeout_ms": round(calibration.timeout_ms, 1),
            "measured_p99_ms": {
                str(k): round(v, 2) for k, v in calibration.measurements.items()
            },
            "calibration_ok": calibration.calibration_ok,
            "fallback_reason": calibration.fallback_reason,
        }
    else:
        calibration_data = {
            "tier": "unknown",
            "sync_cap": 0,
            "timeout_ms": 60.0,
            "measured_p99_ms": {},
            "calibration_ok": False,
            "fallback_reason": "calibration not available",
        }

    # ── Streaming metrics (E-004-S-005) ──────────────────────────────────────
    # app.state.streaming_tracker is set by lifespan startup (E-004-S-005).
    # Fall back to zero values if somehow missing (e.g. pre-E-004 startup).
    streaming_tracker: Optional[StreamingMetricsTracker] = getattr(
        request.app.state, "streaming_tracker", None
    )

    return {
        "scanner": "healthy" if scanner_health.healthy else "error",
        "scanner_mode": config.scanner.mode,
        "entity_set": config.scanner.entity_set,
        "avg_scan_ms": scanner_health.avg_latency_ms,
        "p99_scan_ms": scanner_health.p99_latency_ms,
        "queue_depth": scanner_health.queue_depth,
        "pool_workers": pool_workers,
        "calibration": calibration_data,
        # ── New streaming fields (E-004-S-005) ─────────────────────────────
        "streaming_active": streaming_tracker.active_count if streaming_tracker else 0,
        "window_scan_avg_ms": round(streaming_tracker.window_avg_ms, 4) if streaming_tracker else 0.0,
        "window_scan_p99_ms": round(streaming_tracker.window_p99_ms, 4) if streaming_tracker else 0.0,
        "window_scan_count": streaming_tracker.window_count if streaming_tracker else 0,
    }
