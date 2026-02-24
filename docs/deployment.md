# OnGarde Deployment Guide

## Binding and Network Security

### Default Binding: 127.0.0.1 (Loopback-Only)

OnGarde binds to `127.0.0.1:4242` by default. This is intentional:

- The proxy is designed to sit between your AI agent platform (e.g. OpenClaw) and the LLM provider.
- Both the agent platform and OnGarde run on the same VPS, so loopback-only binding is sufficient.
- **Never expose OnGarde's port directly to the internet.** The port should only be accessible by the AI agent platform on the same machine.

### Configuring the Binding Host

To override the default binding, set `proxy.host` in `.ongarde/config.yaml`:

```yaml
version: 1

proxy:
  host: "127.0.0.1"   # default — recommended for local-only deployments
  port: 4242
```

If you configure `proxy.host: "0.0.0.0"` (all interfaces), OnGarde will start but log a security warning:

```
SECURITY WARNING: OnGarde is configured to bind on 0.0.0.0 (all interfaces).
This exposes the proxy to network-accessible clients.
Recommended: use proxy.host: '127.0.0.1' for local-only access.
```

Only use `0.0.0.0` if you have a Tailscale or VPN network and understand the exposure.

---

## Uvicorn Hardened Defaults

OnGarde ships with hardened uvicorn settings (architecture.md §9.3). These are **non-negotiable**
for security and should not be weakened without an architecture review.

### Via `app/run.py` (recommended — reads config for host/port)

```bash
python -m app.run
```

Or via the installed CLI entry point:

```bash
ongarde
```

### Via CLI (manual start)

```bash
uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 4242 \
  --limit-concurrency 100 \
  --backlog 50 \
  --timeout-keep-alive 5
```

### Hardened Settings Explained

| Setting | Value | Why |
|---------|-------|-----|
| `--limit-concurrency 100` | Max 100 simultaneous connections | Prevents resource exhaustion. New connections beyond this limit receive **HTTP 503**. This matches the httpx connection pool size (also 100). |
| `--backlog 50` | OS-level TCP connection queue | Limits how many pending SYN connections can queue up before being dropped. Reduces SYN flood exposure. |
| `--timeout-keep-alive 5` | 5 second keep-alive timeout | Narrows the Slow Loris attack window. A client that opens a connection but sends data very slowly will be disconnected after 5 seconds of inactivity. Default uvicorn value is 5, but it's important to verify this is not relaxed. |

---

## Request and Response Size Limits

### Request Body Hard Cap (1 MB)

OnGarde rejects request bodies exceeding **1 MB (1,048,576 bytes)** with **HTTP 413** before
any scanning or upstream forwarding occurs. This protects against:

- Memory exhaustion via oversized AI request bodies
- Scan bypass via payload flooding

```
HTTP 413 Payload Too Large
Content-Type: application/json

{
  "error": {
    "message": "Request body too large. Maximum size: 1MB",
    "code": "payload_too_large"
  }
}
```

The limit applies to both:
1. **Requests with `Content-Length`**: rejected immediately on header inspection.
2. **Chunked transfer encoding (no `Content-Length`)**: rejected as bytes accumulate past 1 MB.

### Response Buffer Threshold (512 KB)

OnGarde buffers upstream responses in memory for scanning only if the response body is
**≤ 512 KB (524,288 bytes)**. Larger responses are routed to the streaming scan path:

| Response Size | Handling |
|--------------|---------|
| ≤ 512 KB | Buffered in memory; scanned before forwarding to agent |
| > 512 KB | Streamed to agent via streaming scan path |
| SSE (`text/event-stream`) | Always streamed (per-window scanning via E-004) |

> **Note:** Full streaming scan with per-512-char window scanning is implemented in E-004.
> In the current version (E-001 sprint), the streaming path is a pass-through stub.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ONGARDE_AUTH_REQUIRED` | `true` | Require API key authentication. Set to `false` only for local dev/testing. |
| `ONGARDE_PORT` | `4242` | Override `proxy.port` from config |
| `ONGARDE_CONFIG` | (auto-detect) | Explicit path to config file |
| `DEBUG` | `false` | Enable debug mode: re-enables `/docs` and `/redoc` Swagger UI, enables hot-reload |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `JSON_LOGS` | `true` | Structured JSON logging (set `false` for human-readable dev output) |

---

## API Authentication

API key authentication is **enabled by default** (`ONGARDE_AUTH_REQUIRED=true`). Every request to the OnGarde proxy must include a valid key. Set `ONGARDE_AUTH_REQUIRED=false` only in isolated local dev/testing environments — never in production.

### Creating the First Key

**Option 1 — Init wizard (recommended):**

```bash
npx @ongarde/openclaw init
```

The wizard installs OnGarde, starts it, and prints the first API key during setup.

**Option 2 — Direct API call** (run this once immediately after first start, before enabling auth):

```bash
curl -X POST http://127.0.0.1:4242/dashboard/api/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "production"}'
```

Response:
```json
{
  "key": "ong-xxxxxxxxxxxxxxxxxxxx",
  "name": "production",
  "created_at": "2026-02-24T19:00:00Z"
}
```

> The key is shown **only once** at creation time. Store it securely.

### Passing the Key in Requests

Include one of the following headers on every proxied request:

```
X-OnGarde-Key: ong-xxxxxxxxxxxxxxxxxxxx
```

or

```
Authorization: Bearer ong-xxxxxxxxxxxxxxxxxxxx
```

Both formats are accepted. The `X-OnGarde-Key` header takes precedence if both are present.

### Key Management

Additional keys can be created, listed, and revoked via the dashboard (`/dashboard`) or the key management API (`/dashboard/api/keys/*`). Key management endpoints are rate-limited to **20 requests/minute** per IP.

---

## Quick Start

```bash
# 1. Install OnGarde
pip install -e ".[full]"
python -m spacy download en_core_web_sm

# 2. Create config
mkdir -p .ongarde
cp .ongarde/config.yaml.example .ongarde/config.yaml

# 3. Start the proxy
ongarde
# or: python -m app.run

# 4. Verify
curl http://127.0.0.1:4242/health
# {"status": "ok", "proxy": "running", ...}

# 5. Configure your AI agent platform to use OnGarde
# In OpenClaw: models.providers.baseUrl: http://127.0.0.1:4242/v1
```
