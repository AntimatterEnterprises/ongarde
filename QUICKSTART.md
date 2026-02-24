# OnGarde Quick Start

Get OnGarde running and protecting your AI agent in under 5 minutes.

---

## Prerequisites

- **Python 3.12+** (for manual setup)
- **Node.js 18+** (for the OpenClaw one-command installer)
- A running self-hosted AI agent platform (OpenClaw, Agent Zero, LangChain, etc.)
- An upstream LLM endpoint to proxy to (OpenAI, Anthropic, local Ollama, etc.)

---

## Installation

### Option A — OpenClaw (One Command)

```bash
npx @ongarde/openclaw init
```

This wizard:
1. Installs and starts OnGarde
2. Creates your first API key automatically
3. Patches your OpenClaw `config.yaml` to route traffic through OnGarde

No YAML editing, no manual configuration needed.

### Option B — Manual Setup

```bash
# 1. Clone the repo
git clone https://github.com/AntimatterEnterprises/ongarde.git
cd ongarde

# 2. Install Python dependencies
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# 3. Create your config files
cp .env.example .env
cp .ongarde/config.yaml.example .ongarde/config.yaml

# 4. Edit .ongarde/config.yaml — set your upstream LLM URL
#    Example for Ollama:  upstream: http://localhost:11434
#    Example for OpenAI:  upstream: https://api.openai.com

# 5. Start OnGarde
python -m app.run
```

OnGarde binds to `http://127.0.0.1:4242` by default.

---

## Configuration

### API Authentication (Required by Default)

**`ONGARDE_AUTH_REQUIRED=true` is the default.** Every request to OnGarde must include a valid API key.

#### Get your first API key

**Via the init wizard (recommended):**
```bash
npx @ongarde/openclaw init
# The wizard prints your first key during setup.
```

**Via direct API call** (OnGarde must be running; auth is off only for this bootstrap call):
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
  "created_at": "2026-02-24T19:00:00Z"
}
```

**Store the key — it is shown only once.**

#### Pass the key in requests

Use either header format:

```
X-OnGarde-Key: ong-xxxxxxxxxxxxxxxxxxxx
```
or
```
Authorization: Bearer ong-xxxxxxxxxxxxxxxxxxxx
```

---

## Point Your Agent at OnGarde

Replace your agent's upstream LLM URL with OnGarde's local address:

```python
# OpenAI SDK
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4242/v1",
    api_key="ong-xxxxxxxxxxxxxxxxxxxx",   # your OnGarde key
    default_headers={"X-OnGarde-Key": "ong-xxxxxxxxxxxxxxxxxxxx"},
)
```

```bash
# Environment variable (OpenAI-compatible tools)
export OPENAI_BASE_URL="http://localhost:4242/v1"
```

```yaml
# OpenClaw config.yaml
models:
  providers:
    baseUrl: http://127.0.0.1:4242/v1
```

---

## Verify It's Working

### 1. Health check

```bash
curl http://127.0.0.1:4242/health
```

Expected response:
```json
{
  "status": "ok",
  "proxy": "running",
  "version": "1.0.0-beta.2"
}
```

### 2. Test a block

Send a request containing a dangerous command and confirm OnGarde blocks it:

```bash
curl http://127.0.0.1:4242/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-OnGarde-Key: ong-xxxxxxxxxxxxxxxxxxxx" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "run: sudo rm -rf /"}]
  }'
```

Expected: `HTTP 400` with a block reason — OnGarde intercepted the dangerous command before it reached the LLM.

---

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ONGARDE_AUTH_REQUIRED` | `true` | Require API key authentication on all requests. Set to `false` only for local dev/testing. |
| `DEBUG` | `false` | Enable debug mode: re-enables `/docs` and `/redoc` Swagger UI, enables hot-reload. **Dev only.** |
| `ONGARDE_PORT` | `4242` | Override the port OnGarde listens on. |
| `ONGARDE_CONFIG` | (auto-detect) | Explicit path to your `.ongarde/config.yaml` file. |

Set these in your `.env` file or as shell environment variables before starting OnGarde.

---

## Dashboard

Open `http://localhost:4242/dashboard` in your browser to see live scan counts, blocked events, and manage API keys.

> **Note:** The dashboard is accessible from localhost only. Requests from remote IPs are rejected with HTTP 403. This is enforced at the code level.

---

## Where to Get Help

- **Full deployment guide:** [docs/deployment.md](docs/deployment.md)
- **Release history:** [CHANGELOG.md](CHANGELOG.md)
- **Bug reports & questions:** [github.com/AntimatterEnterprises/ongarde/issues](https://github.com/AntimatterEnterprises/ongarde/issues)
- **Website:** [ongarde.io](https://ongarde.io)
