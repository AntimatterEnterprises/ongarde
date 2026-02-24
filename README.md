# OnGarde 🤺

**Runtime Security for Self-Hosted AI Agents**

OnGarde is a transparent security proxy for self-hosted AI agent platforms. It intercepts every LLM request and response, scans for threats in under 50ms, and blocks credential leaks, dangerous commands, PII, and prompt injection — without changing a line of your agent code.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1%2C222%20passing-brightgreen.svg)](#testing)

---

## What It Does

OnGarde sits between your AI agent and the LLM provider. Zero code changes required — just point your `baseUrl` at OnGarde.

```
Agent → OpenClaw Gateway → OnGarde Proxy → LLM Provider
                                ↑
                         Scans every request
                         and response here
```

**Blocks by default:**
- 🔑 Credential & API key leaks
- 💣 Dangerous shell commands (`rm -rf`, `sudo`, fork bombs)
- 🕵️ Prompt injection attempts
- 🪪 PII (SSNs, credit cards, personal data)
- 📁 Sensitive file access (`.env`, `.ssh/*`, `/etc/passwd`)

**Fail-safe:** errors and timeouts default to BLOCK — security over availability.

---

## Quick Start

### OpenClaw (One Command)

```bash
npx @ongarde/openclaw init
```

Automatically configures OnGarde as your OpenClaw proxy. No YAML editing required.

### Manual Setup

```bash
# Clone and install
git clone https://github.com/AntimatterEnterprises/ongarde.git
cd ongarde
pip install -r requirements.txt

# Configure
cp .env.example .env
cp .ongarde/config.yaml.example .ongarde/config.yaml
# Edit .ongarde/config.yaml with your upstream LLM URL

# Run
python -m app.run
```

Then point your agent at `http://localhost:4242`:

```python
# OpenAI SDK
client = OpenAI(base_url="http://localhost:4242/v1")

# Environment variable
export OPENAI_BASE_URL="http://localhost:4242/v1"
```

> **Authentication is on by default.** `ONGARDE_AUTH_REQUIRED=true` — every request requires an API key. The `npx @ongarde/openclaw init` wizard creates your first key automatically. For manual setup, see the [API key instructions in QUICKSTART.md](QUICKSTART.md#api-authentication-required-by-default).

See [QUICKSTART.md](QUICKSTART.md) for full setup details.

---

## Performance

Benchmarked on a **2 vCPU / 4 GB DigitalOcean Droplet** (recommended production hardware):

| Operation | Input size | p50 | p99 |
|-----------|------------|-----|-----|
| Regex scan (credentials, shell commands) | up to 8 KB | < 0.5ms | < 1ms |
| Full scan (regex + NLP/PII detection) | 100 chars (~75 tokens) | 8ms | 9ms |
| Full scan (regex + NLP/PII detection) | 500 chars (~375 tokens) | 16ms | 20ms |
| Full scan (regex + NLP/PII detection) | 1,000 chars (~750 tokens) | 28ms | 33ms |
| Streaming window scan | 512-char window | < 0.3ms | < 0.2ms |

**Target: < 50ms total overhead — met across all typical LLM prompt sizes.**

OnGarde auto-calibrates at startup: it benchmarks scan latency on your hardware and adjusts the NLP sync threshold accordingly. On slower or single-core machines it automatically reduces the Presidio scan cap to stay within budget — no manual tuning required.

---

## Project Structure

```
ongarde/
├── app/                    # Core proxy application (FastAPI)
│   ├── main.py             # Entry point
│   ├── proxy/              # Request interception & streaming
│   ├── scanner/            # Threat detection engine
│   ├── rules/              # Security rule definitions
│   ├── audit/              # Audit trail (SQLite + Supabase)
│   ├── auth/               # API key management
│   ├── allowlist/          # False-positive recovery
│   ├── dashboard/          # Web dashboard (:4242/dashboard)
│   └── utils/              # Logging, helpers
├── packages/
│   └── openclaw/           # npm CLI (@ongarde/openclaw)
├── tests/                  # 1,222 tests (unit, integration, security)
├── benchmarks/             # Performance benchmarks & results
├── demo/                   # Interactive demo scripts
├── tools/                  # Diagnostic & helper scripts
├── docs/                   # Technical documentation
└── .ongarde/               # Config templates
```

---

## Dashboard

Once running, open `http://localhost:4242/dashboard` to see:
- Live scan counts and block rate
- Recent blocked events with full context
- Scanner health and quota status
- API key management

> **Localhost only.** The dashboard is accessible exclusively from the local machine. Requests from remote IPs are rejected with HTTP 403, enforced at the code level.

---

## Security Model

### Streaming vs Non-Streaming

| Mode | Guarantee |
|------|-----------|
| Non-streaming | **Absolute** — response never forwarded before scan passes |
| Streaming (SSE) | **Best-effort** — per 512-char window with 128-char overlap |

> Streaming limitation: up to one 512-char window (~128 tokens) may reach the agent before termination. Use `stream: false` for absolute guarantees on sensitive workloads.

Full details: [docs/STREAMING_SECURITY_MODEL.md](docs/STREAMING_SECURITY_MODEL.md)

---

## Testing

```bash
# Full suite
pytest tests/ -v

# Security tests only
pytest tests/security/ -v

# With coverage
pytest tests/ --cov=app --cov-report=term-missing
```

1,222 tests. 0 failures.

---

## Documentation

- [QUICKSTART.md](QUICKSTART.md) — Setup and configuration
- [CHANGELOG.md](CHANGELOG.md) — Release history
- [docs/deployment.md](docs/deployment.md) — Production deployment guide

---

## Contributing

Issues and PRs welcome. Please open an issue before starting significant work so we can discuss direction.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Links

- **Website:** [ongarde.io](https://ongarde.io)
- **Issues / Contact:** [github.com/AntimatterEnterprises/ongarde/issues](https://github.com/AntimatterEnterprises/ongarde/issues)
