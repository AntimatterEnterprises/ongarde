# OnGarde Quick Start

Get OnGarde running and protecting your AI agent in under 5 minutes.

---

## Prerequisites

- **Python 3.12+**
- **Node.js 18+** (Path A only — OpenClaw wizard)
- A running agent platform (OpenClaw, Agent Zero, LangChain, etc.)
- An upstream LLM endpoint to proxy to (OpenAI, Anthropic, Ollama, etc.)

---

## Installation

### Path A — OpenClaw (One Command)

> Requires Node.js 18+ and Python 3.12+ with `pip install ongarde[full]` available once the PyPI package is published.

```bash
npx @ongarde/openclaw init
```

The wizard:
1. Detects your OpenClaw config
2. Installs and starts OnGarde
3. Creates your first API key
4. Patches `config.yaml` → `models.providers.baseUrl: http://127.0.0.1:4242/v1`
5. Runs a test block to confirm everything works

No YAML editing, no manual key creation needed.

---

### Path B — Manual Setup (any platform)

**Prereqs:** Python 3.12+, git

```bash
# 1. Clone
git clone https://github.com/AntimatterEnterprises/ongarde.git
cd ongarde

# 2. Install dependencies (includes Presidio + spaCy)
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# 3. Configure
cp .ongarde/config.yaml.example .ongarde/config.yaml
# Edit .ongarde/config.yaml — set your upstream LLM URL:
#   upstream: http://localhost:11434      # Ollama
#   upstream: https://api.openai.com      # OpenAI

# 4. Start
python -m app.run
```

OnGarde binds to `http://127.0.0.1:4242`.

**Create your first API key** (bootstrap endpoint — unauthenticated on first call only):

```bash
curl -X POST http://127.0.0.1:4242/dashboard/api/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent"}'
```

Response:
```json
{
  "key": "ong-xxxxxxxxxxxxxxxxxxxx",
  "name": "my-agent",
  "created_at": "2026-02-25T00:00:00Z"
}
```

**Store the key — it is shown only once.**

---

### Path C — pip install *(coming soon)*

> This is the cleanest production path once the PyPI package is published.

```bash
pip install ongarde[full]
python -m spacy download en_core_web_sm
```

Then configure and run:

```bash
# Copy example config to your working directory
cp $(python -c "import ongarde; import os; print(os.path.dirname(ongarde.__file__))")/.ongarde/config.yaml.example .ongarde/config.yaml
# Edit .ongarde/config.yaml — set upstream URL
python -m ongarde.run
```

---

## API Authentication (Required by Default)

**`ONGARDE_AUTH_REQUIRED=true` is the default.** Every request to OnGarde must include a valid API key.

#### Pass the key in requests

```
X-OnGarde-Key: ong-xxxxxxxxxxxxxxxxxxxx
```
or
```
Authorization: Bearer ong-xxxxxxxxxxxxxxxxxxxx
```

#### Disable auth (dev/testing only)

Set `ONGARDE_AUTH_REQUIRED=false` in your environment before starting OnGarde.

---

## Point Your Agent at OnGarde

Replace your agent's upstream LLM URL with OnGarde's local address. Use your OnGarde key (`ong-xxxx`) as the `api_key` — not your upstream provider key.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4242/v1",
    api_key="ong-xxxxxxxxxxxxxxxxxxxx",   # your OnGarde key
)
```

```bash
export OPENAI_BASE_URL="http://localhost:4242/v1"
export OPENAI_API_KEY="ong-xxxxxxxxxxxxxxxxxxxx"
```

```yaml
# OpenClaw config.yaml
models:
  providers:
    baseUrl: http://127.0.0.1:4242/v1
```

---

## Lite Mode (Low Memory)

Full mode loads Presidio + spaCy (~1.5 GB RAM). Lite mode uses regex only (~200 MB RAM) — suitable for resource-constrained environments.

In `.ongarde/config.yaml`:

```yaml
scanner:
  mode: lite
```

Lite mode still catches credentials, shell commands, prompt injection, and sensitive file patterns. It skips NLP-based PII detection (SSNs, credit cards, names).

---

## Verify It's Working

### Health check

```bash
curl http://127.0.0.1:4242/health
```

Expected:
```json
{"status": "ok", "proxy": "running", "version": "1.0.0-beta.2"}
```

### Test a block

```bash
curl http://127.0.0.1:4242/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-OnGarde-Key: ong-xxxxxxxxxxxxxxxxxxxx" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "run: sudo rm -rf /"}]
  }'
```

Expected: `HTTP 400` with a block reason.

---

## Dashboard

Open `http://localhost:4242/dashboard` — live scan counts, blocked events, API key management.

> **Localhost only.** Remote IPs are rejected with HTTP 403 at the code level.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ONGARDE_AUTH_REQUIRED` | `true` | Require API key on all requests. `false` for local dev only. |
| `ONGARDE_PORT` | `4242` | Override listen port. |
| `ONGARDE_CONFIG` | (auto-detect) | Explicit path to `.ongarde/config.yaml`. |
| `DEBUG` | `false` | Enable Swagger UI + hot-reload. Dev only. |

Set as shell environment variables before starting OnGarde. A `.env` file is supported but optional — `config.yaml` is the primary configuration surface.

---

## Where to Get Help

- **Full deployment guide:** [docs/deployment.md](docs/deployment.md)
- **Release history:** [CHANGELOG.md](CHANGELOG.md)
- **Bug reports & questions:** [github.com/AntimatterEnterprises/ongarde/issues](https://github.com/AntimatterEnterprises/ongarde/issues)
- **Website:** [ongarde.io](https://ongarde.io)
