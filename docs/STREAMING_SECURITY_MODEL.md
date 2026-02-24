# OnGarde Streaming Security Model

**Acceptance Criterion:** AC-E004-08  
**Story:** E-004-S-001, E-004-S-002, E-004-S-003  
**Last Updated:** 2026-02-24

---

## Overview

OnGarde intercepts LLM responses at the proxy layer. For **non-streaming** (buffered)
responses, the entire response is scanned before being forwarded — this provides
**absolute** protection: no content reaches the agent unless it passes all security
checks.

For **streaming** (SSE) responses, the model begins sending tokens immediately and
OnGarde must scan content in real-time. This requires a window-based approach with
an inherent, documented limitation.

---

## How Streaming Scanning Works

### Window-Based Accumulation

Streaming responses arrive as a sequence of small SSE chunks. OnGarde's
`StreamingScanner` (in `app/scanner/streaming_scanner.py`) accumulates these chunks
into a **512-character window buffer**. When the buffer reaches 512 characters, a
regex scan is triggered synchronously before the window is rotated.

```
SSE chunks → window_buffer (512 chars) → regex_scan() → PASS/BLOCK
```

Key constants:

| Constant        | Value | Purpose                                       |
|-----------------|-------|-----------------------------------------------|
| `WINDOW_SIZE`   | 512   | Characters accumulated before each scan       |
| `OVERLAP_SIZE`  | 128   | Overlap chars prepended from previous window  |

### Cross-Boundary Protection (128-char Overlap)

To prevent credentials or threat patterns from evading detection by spanning two
windows, the last 128 characters of each window are prepended to the next scan's
input. This overlap buffer ensures a secret split across a window boundary — e.g.,
a 64-character API key arriving as 32+32 chars at the boundary — is still detected.

```
Window N scan text:   [overlap_128][window_N_512]
Window N+1 scan text: [last 128 of window_N][window_N+1_512]
```

### Scan Technology

Streaming windows are scanned with **regex-only** (google-re2). Presidio (ML-based
PII detection) is **never** called synchronously on the streaming window path — it
runs as a background advisory task only. This keeps per-window scan latency to
≤ 0.5ms p99 (architecture.md §2.2).

---

## ⚠️ Mandatory Limitation — AC-E004-08

**This limitation must never be removed from product copy or documentation.**

> **Streaming is best-effort, not absolute.**  
> Because scanning is triggered every 512 characters (~128 tokens), up to one
> 512-char window (~128 tokens) **may reach the agent** before a threat is detected
> and the stream is aborted.

Non-streaming (`stream: false`) provides absolute guarantees. For workloads where
zero malicious content tolerance is required, disable streaming.

### Comparison

| Mode            | Security Guarantee | Latency profile          |
|-----------------|--------------------|--------------------------|
| Non-streaming   | **Absolute** — no content forwarded before full scan passes | Higher (full buffer) |
| Streaming (SSE) | **Best-effort** — up to one 512-char window (~128 tokens) may reach the agent before termination | Lower (incremental) |

---

## Abort Mechanism

When a streaming window scan returns `BLOCK`, OnGarde immediately:

1. **Stops forwarding** further chunks from the upstream LLM.
2. **Emits an SSE abort sequence** via `emit_stream_abort()` in
   `app/proxy/streaming.py` (E-004-S-003):

   ```
   data: [DONE]\n\n
   event: ongarde_block\ndata: {payload_json}\n\n
   ```

   - `data: [DONE]` — closes the SSE stream for standard clients (prevents hanging).
   - `event: ongarde_block` — signals the block reason to OnGarde-aware clients.
     Clients that do not recognise `ongarde_block` silently discard it per the SSE
     spec (§8.9 — unknown event types are ignored).

3. **Records the block** in metrics via `StreamingMetricsTracker` (E-004-S-005).

Both SSE events are emitted from in-memory byte constants — total abort latency is
< 1ms (AC-E004-03).

### Abort Payload Fields

| Field              | Description                                              |
|--------------------|----------------------------------------------------------|
| `scan_id`          | ULID for the blocked request                            |
| `rule_id`          | Matched rule (e.g. `CREDENTIAL_API_KEY`)                |
| `risk_level`       | `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`                  |
| `tokens_delivered` | Byte-approximated token count delivered before abort (±20%, AC-E004-04) |
| `timestamp`        | ISO-8601 UTC timestamp of the abort event               |
| `redacted_excerpt` | Redacted snippet of the matched content (optional)      |
| `suppression_hint` | Policy suppression suggestion for false positives (optional) |

---

## Fail-Safe Behaviour

Per architecture.md §5.1, OnGarde **always blocks on error**:

- If `regex_scan()` raises any exception during a window scan, the stream is
  immediately aborted with `rule_id=SCANNER_ERROR`, `risk_level=CRITICAL`.
- If `regex_scan()` returns `None` (unexpected), it is also treated as BLOCK.
- Once `StreamingScanner.aborted=True`, all further `add_content()` calls return
  the cached BLOCK result without re-scanning (fast path).

This means a scanner failure can **never** allow content to pass silently.

---

## `strict_mode` Configuration (Stub — Not Implemented in v1)

OnGarde's configuration (`app/config.py`, `.ongarde/config.yaml`) exposes a
`strict_mode` boolean key:

```yaml
strict_mode: false   # see docs/STREAMING_SECURITY_MODEL.md
```

**In v1, `strict_mode` is a stub and is not implemented.** If set to `true`,
OnGarde emits a warning log:

```
strict_mode is not implemented in v1 — ignored
```

**Planned behaviour (v2+):** When `strict_mode: true`, streaming will be disabled
entirely at the proxy layer — all requests will be forced to `stream: false`,
providing absolute protection at the cost of streaming latency. Until this is
implemented, use `stream: false` in your LLM client configuration for equivalent
behaviour.

---

## Architecture References

- `app/scanner/streaming_scanner.py` — `StreamingScanner`, `WINDOW_SIZE`, `OVERLAP_SIZE`
- `app/proxy/streaming.py` — `emit_stream_abort()`, `StreamAbortPayload`
- `app/proxy/engine.py` — `_stream_response_scan()`, `_extract_content_from_sse_message()`
- `app/config.py` — `strict_mode` stub and warning
- `architecture.md §2.2` — Streaming scanner design constraints
- `architecture.md §5.1` — Fail-safe / block-on-error policy

---

## Summary

| Property              | Value                                      |
|-----------------------|--------------------------------------------|
| Scan window           | 512 characters                             |
| Overlap buffer        | 128 characters (cross-boundary detection)  |
| Scan engine           | google-re2 (regex only, no Presidio)       |
| Per-window latency    | ≤ 0.5ms p99                               |
| Abort latency         | < 1ms (AC-E004-03)                         |
| Tokens that may reach the agent | Up to ~128 tokens (one 512-char window) |
| Failure mode          | BLOCK (fail-safe)                          |
| `strict_mode` v1      | Stub — not implemented (warning emitted)   |
