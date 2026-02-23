# @ongarde/openclaw

> **OnGarde CLI** — Install, configure, and manage the OnGarde runtime content security proxy.

This npm package is implemented in **E-007** (Onboarding Flow, Sprint 4).  
This file documents the **`/health` endpoint contract** that the `start` subcommand depends on.

---

## Installation

```bash
npx @ongarde/openclaw init
```

---

## `npx @ongarde/openclaw start`

Starts the OnGarde proxy and waits until it is ready to accept traffic.

### Exit behaviour

| Condition | Exit code |
|-----------|-----------|
| `/health` returns HTTP 200 within 30 seconds of process start | **0** (success) |
| Timeout: 30 seconds elapsed without a 200 response | **1** (failure) |
| OnGarde process exits with a non-zero code during startup | **1** (failure) |

### Health check polling

- Polls `GET http://localhost:4242/health` every **500 ms**
- Accepts **HTTP 200** with `"status": "ok"` as the ready signal
- **HTTP 503** (`"status": "starting"`) means startup is still in progress — continue polling
- Any other status code or network error: retry until timeout

### Ready signal specification

The `/health` endpoint (implemented in `app/health.py`, E-001-S-007) returns:

**HTTP 503 — not ready** (body wrapped in `{"error": {...}}` by the global exception handler):
```json
{
  "error": {
    "status": "starting",
    "scanner": "initializing",
    "message": "OnGarde is starting up. Scanner warming up..."
  }
}
```

**HTTP 200 — ready** (all fields present):
```json
{
  "status": "ok",
  "proxy": "running",
  "scanner": "healthy",
  "scanner_mode": "full",
  "connection_pool_size": 100,
  "avg_scan_ms": 0.0,
  "queue_depth": 0,
  "deployment_mode": "self-hosted",
  "audit_path": "/root/.ongarde/audit.db"
}
```

The `start` command MUST poll for HTTP 200 (not parse the JSON body) — the status code
is the authoritative ready signal.

### Implementation note for E-007

The `start` subcommand must:

1. Spawn the proxy process (e.g. `uvicorn app.main:app --host 127.0.0.1 --port 4242 ...`)
2. Enter a polling loop:
   ```
   deadline = now() + 30_000ms
   while now() < deadline:
       try:
           response = GET http://localhost:4242/health (timeout: 2s)
           if response.status == 200:
               print("✓ OnGarde ready. En Garde. 🤺")
               exit(0)
       except NetworkError:
           pass  # proxy not yet listening — continue polling
       sleep(500ms)
   print("✗ Timeout: OnGarde did not become ready within 30 seconds.")
   exit(1)
   ```
3. Also monitor the spawned process PID; if it exits non-zero before `/health` returns
   200, exit 1 immediately with the proxy's stderr output.

---

## Other subcommands

| Command | Description | Story |
|---------|-------------|-------|
| `npx @ongarde/openclaw init` | 4-step onboarding wizard | E-007-S-001 |
| `npx @ongarde/openclaw start` | Start proxy + wait for ready | E-007-S-006 |
| `npx @ongarde/openclaw status` | Show current proxy status | E-007-S-006 |
| `npx @ongarde/openclaw uninstall` | Stop proxy, restore baseUrl | E-007-S-006 |

---

## Health endpoint reference

Full specification: `app/health.py`

| Endpoint | Ready? | Description |
|----------|--------|-------------|
| `GET /health` | Any | Primary health check. 503 during startup, 200 when ready. |
| `GET /health/scanner` | Any | Detailed scanner metrics. 503 during startup, 200 when ready. |

---

*Package scaffold implemented in E-007-S-001. This README delivers the AC-E001-10 exit gate contract (E-001-S-007).*
