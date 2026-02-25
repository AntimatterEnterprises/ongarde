# @ongarde/openclaw

> **OnGarde CLI for OpenClaw** — Install, configure, and run the OnGarde runtime content security proxy in seconds.

[![npm version](https://img.shields.io/npm/v/@ongarde/openclaw)](https://www.npmjs.com/package/@ongarde/openclaw)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

OnGarde is a lightweight, self-hosted security proxy that sits between your AI agent platform (like [OpenClaw](https://ongarde.io)) and your LLM provider. It scans every request and response for PII, prompt injection, policy violations, and custom rules — with sub-50 ms overhead.

---

## Quick Start

No global install needed. Just run:

```bash
npx @ongarde/openclaw init
```

The `init` wizard will:
1. Check prerequisites (Python 3.12+, pip)
2. Install the `ongarde` Python package
3. Generate a default config (`~/.ongarde/config.yaml`)
4. Configure your AI platform to route through the proxy

---

## Commands

| Command | Description |
|---------|-------------|
| `npx @ongarde/openclaw init` | Interactive setup wizard (first-time onboarding) |
| `npx @ongarde/openclaw start` | Start the proxy and wait until it's ready |
| `npx @ongarde/openclaw status` | Show current proxy status and health metrics |
| `npx @ongarde/openclaw uninstall` | Stop proxy and restore your original AI platform config |

---

## Prerequisites

- **Node.js** 18 or later
- **Python** 3.12 or later
- **pip** (comes with Python)

The `init` command will check these automatically and tell you what to install if anything is missing.

---

## How It Works

OnGarde acts as a transparent HTTP proxy between your AI client and your LLM provider:

```
AI Agent / OpenClaw
        │
        ▼
  OnGarde Proxy  ←── scans requests & responses
  (localhost:4242)    • PII detection
        │             • Prompt injection detection
        ▼             • Custom allow/block rules
   LLM Provider       • Audit logging
  (OpenAI, etc.)
```

Configuration is done via `~/.ongarde/config.yaml`. Full documentation at [ongarde.io](https://ongarde.io).

---

## Python Package

This CLI installs the `ongarde` Python package behind the scenes. If you prefer to manage it directly:

```bash
pip install ongarde
ongarde  # start the proxy
```

---

## Links

- 🌐 **Website:** [ongarde.io](https://ongarde.io)
- 📦 **PyPI:** [pypi.org/project/ongarde](https://pypi.org/project/ongarde/)
- 🐛 **Issues:** [github.com/AntimatterEnterprises/ongarde/issues](https://github.com/AntimatterEnterprises/ongarde/issues)
- 📖 **Source:** [github.com/AntimatterEnterprises/ongarde](https://github.com/AntimatterEnterprises/ongarde)

---

## License

MIT © [OnGarde](https://ongarde.io)
