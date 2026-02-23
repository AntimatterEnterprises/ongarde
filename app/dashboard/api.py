"""Dashboard API endpoints for OnGarde — E-008-S-008, E-009-S-005.

All endpoints are unauthenticated (localhost binding is the security boundary).
Uses app.state.audit_backend for all data queries via the AuditBackend protocol.

Routes (all prefixed with /dashboard/api/ in main.py for data endpoints):
    GET /dashboard     — serve index.html (mounted at /dashboard in main.py)
    GET /dashboard/    — serve index.html
    GET /status        — proxy status for Feature 1 (status card)
    GET /events        — recent BLOCK + ALLOW_SUPPRESSED events for Feature 5
    GET /counters      — request/block counts for Features 2+3
    GET /health        — scanner health for Feature 4
    GET /quota         — quota usage for Feature 6
    GET /config-status — hot-reload status (last_reload_at, allowlist_count) — E-009-S-005

Stories: E-008-S-001 (router placeholder), E-008-S-008 (full implementation),
         E-009-S-005 (config-status endpoint + notify_config_reloaded)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from app.audit.protocol import EventFilters
from app.utils.health import check_scanner_health
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["dashboard"])

# Track module load time as a proxy for uptime (reset on restart)
_STARTUP_TIME = time.time()

# ─── System rule IDs that have no suppression_hint ────────────────────────────
_SYSTEM_RULE_IDS = frozenset({
    "SCANNER_ERROR",
    "SCANNER_TIMEOUT",
    "QUOTA_EXCEEDED",
    "SCANNER_UNAVAILABLE",
})

# ─── Config hot-reload tracker — E-009-S-005 ─────────────────────────────────
# In-memory dict: updated by notify_config_reloaded() (called by watchfiles watcher).
# Dashboard polls GET /config-status and shows a toast when last_reload_at advances.
_config_status: dict = {
    "last_reload_at": None,    # ISO timestamp of last successful hot-reload
    "allowlist_count": 0,      # Number of active allowlist entries after last reload
}


def notify_config_reloaded(allowlist_count: int) -> None:
    """Called by AllowlistLoader.start_watcher() after a successful hot-reload.

    Updates the in-memory config status so the dashboard can show a toast
    notification within the next 10-second poll cycle.

    AC-E009-05 / AC-E008-09: toast appears within 15 seconds of file save.
    """
    _config_status["last_reload_at"] = datetime.now(timezone.utc).isoformat()
    _config_status["allowlist_count"] = allowlist_count
    logger.info(
        "Config hot-reload notified",
        allowlist_count=allowlist_count,
        last_reload_at=_config_status["last_reload_at"],
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_suppression_hint(rule_id: Optional[str]) -> Optional[str]:
    """Generate a suppression hint YAML snippet for a given rule_id.

    Returns None for system failure rule IDs (no suppression applicable).
    Per AC-E009-06: policy blocks have a non-null hint; system blocks have null.
    """
    if not rule_id or rule_id in _SYSTEM_RULE_IDS:
        return None
    return (
        f"allowlist:\n"
        f"  - rule_id: {rule_id}\n"
        f'    note: "describe why this is a false positive"'
    )


def _require_ready(request: Request) -> None:
    """Raise HTTP 503 if the proxy is not ready."""
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(
            status_code=503,
            detail={"status": "starting", "message": "OnGarde is starting up"},
        )


# ─── GET /status ──────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status(request: Request) -> dict:
    """Feature 1: Proxy status for the dashboard status card.

    Response:
        proxy:          "running"
        port:           4242
        scanner:        "healthy" | "degraded" | "error" | "unavailable"
        scanner_mode:   "full" | "lite"
        uptime_seconds: int

    Returns HTTP 503 when proxy is not yet ready (startup in progress).
    """
    _require_ready(request)

    config = request.app.state.config
    scan_pool = request.app.state.scan_pool
    latency_tracker = getattr(request.app.state, "latency_tracker", None)

    scanner_health = await check_scanner_health(
        scan_pool=scan_pool,
        latency_tracker=latency_tracker,
    )

    if scanner_health.healthy:
        scanner_status = "healthy"
    elif scan_pool is None:
        scanner_status = "unavailable"
    else:
        scanner_status = "degraded"

    return {
        "proxy": "running",
        "port": config.proxy.port,
        "scanner": scanner_status,
        "scanner_mode": config.scanner.mode,
        "uptime_seconds": int(time.time() - _STARTUP_TIME),
    }


# ─── GET /events ──────────────────────────────────────────────────────────────


@router.get("/events")
async def get_events(
    request: Request,
    limit: int = 20,
    include_suppressed: bool = True,
) -> dict:
    """Feature 5: Recent blocked events for the dashboard events table.

    Query params:
        limit:               max events to return (1–50, default 20)
        include_suppressed:  include ALLOW_SUPPRESSED events (default true)

    Response:
        events: list of event objects with all dashboard-required fields
        total:  number of events returned

    Security: redacted_excerpt is already redacted by the scanner pipeline
    (E-002-S-005 contract). The dashboard MUST NOT display raw request bodies.
    """
    _require_ready(request)

    backend = request.app.state.audit_backend
    action_in = ["BLOCK", "ALLOW_SUPPRESSED"] if include_suppressed else ["BLOCK"]
    safe_limit = max(1, min(limit, 50))

    events = await backend.query_events(EventFilters(
        action_in=action_in,
        limit=safe_limit,
        offset=0,
    ))

    return {
        "events": [
            {
                "scan_id": e.scan_id,
                "timestamp": e.timestamp.isoformat(),
                "action": e.action,
                "rule_id": e.rule_id,
                "risk_level": e.risk_level,
                "direction": e.direction,
                # redacted_excerpt: already sanitized (E-002-S-005), cap at 100 chars
                "redacted_excerpt": (e.redacted_excerpt or "")[:100],
                "suppression_hint": _make_suppression_hint(e.rule_id),
                "test": e.test,
                # advisory_presidio_entities for detail panel (never contains raw text)
                "advisory_entities": e.advisory_presidio_entities or [],
                # E-009-S-005: allowlist_rule_id for ALLOW_SUPPRESSED events
                "allowlist_rule_id": e.allowlist_rule_id,
            }
            for e in events
        ],
        "total": len(events),
    }


# ─── GET /counters ────────────────────────────────────────────────────────────


@router.get("/counters")
async def get_counters(request: Request) -> dict:
    """Features 2+3: Request and block counters with risk breakdown.

    Response:
        requests:       {today: int, month: int}
        blocks:         {today: int, month: int}
        risk_breakdown: {CRITICAL: int, HIGH: int, MEDIUM: int, LOW: int}

    Risk breakdown counts are for TODAY only and must sum to blocks.today.
    Counts sourced via AuditBackend.count_events() (AC-E008-10).
    """
    _require_ready(request)

    backend = request.app.state.audit_backend
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Request counts (all actions)
    today_requests = await backend.count_events(EventFilters(since=today_start))
    month_requests = await backend.count_events(EventFilters(since=month_start))

    # Block counts only
    today_blocks = await backend.count_events(
        EventFilters(action="BLOCK", since=today_start)
    )
    month_blocks = await backend.count_events(
        EventFilters(action="BLOCK", since=month_start)
    )

    # Risk breakdown (today's blocks by risk level — must sum to today_blocks)
    breakdown: dict[str, int] = {}
    for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        breakdown[level] = await backend.count_events(
            EventFilters(action="BLOCK", risk_level=level, since=today_start)
        )

    return {
        "requests": {"today": today_requests, "month": month_requests},
        "blocks": {"today": today_blocks, "month": month_blocks},
        "risk_breakdown": breakdown,
    }


# ─── GET /health ──────────────────────────────────────────────────────────────


@router.get("/health")
async def get_dashboard_health(request: Request) -> dict:
    """Feature 4: Scanner health card data.

    Response:
        scanner:      "healthy" | "degraded" | "error"
        scanner_mode: "full" | "lite"
        avg_scan_ms:  float
        p99_scan_ms:  float
        queue_depth:  int
        entity_set:   list[str]
        calibration:  {tier, sync_cap, timeout_ms, calibration_ok} | null

    Delegates to the same data sources as /health/scanner but in a
    dashboard-friendly format (no raw calibration measurement dict).
    """
    _require_ready(request)

    config = request.app.state.config
    scan_pool = request.app.state.scan_pool
    latency_tracker = getattr(request.app.state, "latency_tracker", None)
    calibration = getattr(request.app.state, "calibration", None)

    scanner_health = await check_scanner_health(
        scan_pool=scan_pool,
        latency_tracker=latency_tracker,
    )

    calib_data = None
    if calibration is not None:
        calib_data = {
            "tier": calibration.tier,
            "sync_cap": calibration.sync_cap,
            "timeout_ms": round(calibration.timeout_ms, 1),
            "calibration_ok": calibration.calibration_ok,
        }

    return {
        "scanner": "healthy" if scanner_health.healthy else "error",
        "scanner_mode": config.scanner.mode,
        "avg_scan_ms": round(scanner_health.avg_latency_ms, 1),
        "p99_scan_ms": round(scanner_health.p99_latency_ms, 1),
        "queue_depth": scanner_health.queue_depth,
        "entity_set": config.scanner.entity_set,
        "calibration": calib_data,
    }


# ─── GET /quota ───────────────────────────────────────────────────────────────


@router.get("/quota")
async def get_quota(request: Request) -> dict:
    """Feature 6: Quota display.

    For self-hosted installs (no SUPABASE_URL):
        Returns {self_hosted: true, used: int, limit: null, audit_path: str}

    For managed installs (SUPABASE_URL set):
        Returns {self_hosted: false, used: int, limit: int, reset_date: str|null}

    The progress bar state is determined client-side:
        < 70%  → normal (green)
        ≥ 70%  → warn (amber)
        ≥ 95%  → near-limit (red)
        100%   → limit-reached (red + CTA)
    """
    _require_ready(request)

    config = request.app.state.config
    backend = request.app.state.audit_backend
    is_self_hosted = not bool(os.environ.get("SUPABASE_URL"))

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = await backend.count_events(EventFilters(since=month_start))

    if is_self_hosted:
        # Self-hosted: no quota limit; show audit storage path
        audit_path = os.path.expanduser(
            config.audit.path if hasattr(config, "audit") and hasattr(config.audit, "path")
            else "~/.ongarde/audit.db"
        )
        return {
            "self_hosted": True,
            "used": used,
            "limit": None,
            "reset_date": None,
            "audit_path": audit_path,
        }
    else:
        # Managed install: quota from config or Supabase (stub: 1000/month)
        monthly_limit = getattr(
            getattr(config, "quota", None), "monthly_limit", 1000
        )
        return {
            "self_hosted": False,
            "used": used,
            "limit": monthly_limit,
            "reset_date": None,  # populated from Supabase in Phase 2
            "audit_path": None,
        }


# ─── GET /config-status ───────────────────────────────────────────────────────


@router.get("/config-status")
async def get_config_status(request: Request) -> dict:
    """E-009-S-005: Hot-reload status for the dashboard toast notification.

    Polled every 10 seconds alongside other dashboard data.
    When last_reload_at advances between polls, the dashboard shows a toast:
    "● Config reloaded — allowlist updated"

    Response:
        last_reload_at:  ISO timestamp of last successful allowlist reload, or null.
        allowlist_count: Number of active allowlist entries after last reload.

    AC-E008-09: Toast appears within 15 seconds of file save (10s poll + ~1s reload).
    """
    # No _require_ready check — config-status is informational and safe to return
    # even during startup. Returns null last_reload_at when no reload has occurred.
    return {
        "last_reload_at": _config_status["last_reload_at"],
        "allowlist_count": _config_status["allowlist_count"],
    }
