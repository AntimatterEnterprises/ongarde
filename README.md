# OnGarde.io
**Firewall for Autonomy**

A zero-trust security proxy for agentic AI frameworks that intercepts, audits, and blocks dangerous operations before they execute.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## 🎯 What is OnGarde?

**Runtime Security for Open-Source Agent Platforms**

OnGarde provides transparent runtime content security for self-hosted AI agent platforms like OpenClaw, Agent Zero, CrewAI, and LangChain. Protects against credential leaks, dangerous commands, and prompt injection without configuration overhead.

**Website:** https://ongarde.io

Core capabilities:

- **Runtime Content Scanning:** Inspects LLM requests and responses in < 50ms
- **Credential Leak Prevention:** Blocks API keys, passwords, .env files, secrets
- **Dangerous Command Detection:** Stops `sudo`, `rm -rf`, fork bombs, system mods
- **PII Detection & Redaction:** Identifies SSNs, credit cards, personal data
- **Prompt Injection Protection:** Hard blocks suspicious patterns
- **Complete Audit Trail:** Logs every scan result with full context

## 🏗️ Infrastructure Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Proxy Logic** | Python/FastAPI | High-performance async handling of agent requests |
| **Backend Host** | Railway | Auto-scaling with simple GitHub integration |
| **Data & Auth** | Supabase | Manages audit_logs, user authentication, and RLS |
| **Frontend** | Vercel | Hosts ongarde.io marketing site and user dashboard |
| **Network/WAF** | Cloudflare | DNS management, DDoS protection, SSL termination |

## 📂 Project Structure

```
ongarde.io/
├── .cursor/
│   └── rules/              # Cursor AI coding guidelines
├── app/
│   ├── main.py            # FastAPI entry point
│   ├── proxy/             # Request interception & streaming
│   │   ├── engine.py      # Main proxy logic
│   │   └── streaming.py   # Response streaming utilities
│   ├── rules/             # Security scanning (The Parry)
│   │   ├── scanner.py     # Core security logic
│   │   └── definitions.py # Blocked commands & patterns
│   ├── db/                # Supabase integration
│   │   └── supabase_client.py
│   └── utils/
│       └── logger.py      # Structured logging
├── docs/
│   └── BRD.md            # Business Requirements Document
├── tests/
│   └── test_security.py  # Security test suite
├── CHANGELOG.md          # Sprint tracking and change history
├── QUICKSTART.md         # Developer quick reference
└── requirements.txt       # Python dependencies
```

## 🚀 Quick Start

### For OpenClaw Users (Recommended)

**Coming Soon:**
```bash
# One command to secure your OpenClaw installation
npx @ongarde/openclaw init
```

Automatically protects all skills with zero configuration needed.

### Manual Proxy Setup (For Teams)

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/ongarde.io.git
cd ongarde.io
```

2. **Set up environment**
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Run the proxy**
```bash
uvicorn app.main:app --reload --port 8000
```

5. **Test the proxy**
```bash
curl http://localhost:8000/health
```

### Configure Your Tools

Point API calls through OnGarde:

```python
# Python
client = OpenAI(base_url="http://localhost:8000/v1")

# Environment variable
export OPENAI_BASE_URL="http://localhost:8000/v1"
```

## 🔒 Security Features

### Blocked by Default
- **Shell Commands:** `sudo`, `rm -rf`, `dd`, `mkfs`, `fdisk`, fork bombs
- **File Access:** `.env`, `.ssh/*`, `id_rsa`, `credentials.*`, `/etc/passwd`
- **Network:** Reverse shells, netcat listeners, eval pipes

### PII Detection
- Social Security Numbers
- Credit card numbers
- API keys and passwords
- Email addresses and phone numbers

### Fail-Safe Operation
- Default to **BLOCK** if scans error or timeout
- < 50ms scanning overhead (performance critical)
- Complete audit trail of all decisions

### Security Model: Streaming vs Non-Streaming

OnGarde provides **different security guarantees** for streaming and non-streaming responses:

| Mode | Guarantee |
|------|-----------|
| **Non-streaming responses** | **Absolute** — body never forwarded before scan PASS |
| **Streaming responses (SSE)** | **Best-effort** — per 512-char window scan (≤ 0.5ms/window) |

> **Streaming limitation:** Up to one 512-character window (≈ 128 tokens) of threat content may reach the agent before stream termination. This is inherent to streaming scan design and not a bug.

Streaming security details:
- Regex patterns scanned per 512-char window (google-re2, ≤ 0.5ms p99)
- 128-char overlap buffer detects threats split across window boundaries
- Background Presidio advisory scan on accumulated buffer (non-blocking)
- `ongarde_block` SSE event emitted before stream closes (with `data: [DONE]`)
- All BLOCK events recorded in audit trail with `tokens_delivered` count

For workloads requiring **absolute** streaming protection: use `stream: false` in  
your LLM client configuration (non-streaming mode provides an absolute guarantee).

📖 Full specification: [docs/STREAMING_SECURITY_MODEL.md](docs/STREAMING_SECURITY_MODEL.md)

## 📊 Dashboard

View your security metrics at the OnGarde dashboard:
- Real-time blocked attempt monitoring
- Audit log history
- PII redaction statistics
- Performance metrics

## 🧪 Testing

Run the test suite:

```bash
pytest tests/ -v
```

Run security tests:

```bash
pytest tests/test_security.py -v
```

## 📖 Documentation

- [Business Requirements Document](docs/BRD.md) - Product vision and roadmap
- [Quick Start Guide](QUICKSTART.md) - Developer quick reference
- [Changelog](CHANGELOG.md) - Sprint tracking and progress
- [Cursor Rules](.cursor/rules/) - Coding standards and guidelines

## 🛣️ Roadmap

### MVP (Current)
- ✅ FastAPI proxy with streaming
- ✅ Security scanner (The Parry)
- ✅ Supabase audit logging
- ✅ CLI security scanning tool

### Post-MVP
- 🔮 AI-powered threat detection
- 🔮 Custom security rules
- 🔮 Team collaboration features
- 🔮 Advanced analytics dashboard
- 🔮 Multi-provider support (Google, Cohere)

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 Links

- **Website:** [ongarde.io](https://ongarde.io) (launching soon)
- **Documentation:** [GitHub Docs](docs/)
- **Issue Tracker:** [GitHub Issues](https://github.com/yourusername/ongarde.io/issues)
- **Diagnostic Tool:** [tools/openclaw-diagnostic.sh](tools/openclaw-diagnostic.sh)

## 💬 Support

- **Issues:** [GitHub Issues](https://github.com/yourusername/ongarde.io/issues)
- **Security:** security@ongarde.io
- **General:** hello@ongarde.io

---

**Built with ❤️ for the agentic AI community**
