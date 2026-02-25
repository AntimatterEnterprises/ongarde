# Setup Generator — Feature Backlog

> **Product:** OnGarde Quick Start — AI Orchestrator Setup Generator
> **Status:** POC v1 (Ubuntu-only, client-side script generation)
> **Last updated:** 2026-02-25

This is the living backlog for the Setup Generator tool. Items are loosely prioritized
top-to-bottom within each category. Add new items as they come up during development.

---

## 🔴 High Priority — Next Version

### Branch / Source Selection
- [ ] **Branch selector for OnGarde** — dropdown: `main`, `dev`, or custom branch/tag/commit SHA
- [ ] **Branch selector for BMad** — dropdown: `main`, `dev`, or custom branch/tag
- [ ] **Branch selector for OpenClaw** — currently installs latest npm release; add ability to pin to a specific version (`@2026.2.x`)
- [ ] **Release vs. Git source toggle** — for OnGarde: `pip install ongarde` (PyPI, when published) vs. `git clone` (current)

### Agent Configuration (deeper)
- [ ] **Skill assignment per agent** — each agent gets a set of enabled skills (TTS voice, web search, camera, calendar, etc.) with per-skill API keys (e.g., ElevenLabs key for TTS, Brave key for web_search)
- [ ] **Per-agent API key override** — assign a different LLM provider/key to individual agents (e.g., Amelia uses Claude Sonnet, Winston uses Claude Opus)
- [ ] **Agent personality presets** — quick presets beyond BMad defaults (e.g., "senior engineer", "startup PM", "security auditor")
- [ ] **Custom system prompt field** — textarea for additional persona instructions per agent

### UX Improvements
- [ ] **Config import/export** — save current form state as JSON, re-load it later ("config profiles")
- [ ] **Test SSH connection** button — validate VPS IP + credentials before generating the script
- [ ] **Script validation** — run shellcheck on the generated output and surface errors in the preview
- [ ] **Progress indicator** during script generation (currently instant, but future server-side rendering may be slower)

---

## 🟡 Medium Priority

### Multi-OS Support
- [ ] **Debian 12 (Bookworm)** — very close to Ubuntu; mostly package manager differences
- [ ] **Rocky Linux 9 / AlmaLinux 9** — for enterprise VPS users (requires `dnf`, `firewalld` instead of `ufw`)
- [ ] **Raspberry Pi OS (arm64)** — detect architecture in script and swap arm64 Node.js binary
- [ ] **macOS (local dev)** — Homebrew-based install path, launchd instead of systemd

### Security & Networking
- [ ] **Domain + SSL** — optional Nginx reverse proxy config + Let's Encrypt certbot setup; exposes all services under subdomains (`openclaw.yourdomain.com`, `dashboard.yourdomain.com`, `files.yourdomain.com`)
- [ ] **Tailscale integration** — option to expose services only over Tailscale network instead of public ports; generate Tailscale install + auth key section
- [ ] **Custom SSH key generation** — generate a fresh ed25519 keypair as part of the install, display private key in completion card
- [ ] **UFW custom rules** — allow user to add arbitrary extra allowed IPs/ports from the UI
- [ ] **OnGarde API key setup** — generate and configure a `ong-*` bearer token for the OnGarde dashboard/API at install time

### Services
- [ ] **Nginx reverse proxy** — optional; single entry point with path routing instead of per-service ports
- [ ] **Docker Compose alternative** — generate a `docker-compose.yml` instead of (or alongside) the bash script
- [ ] **Upgrade script** — separate generated script for in-place upgrades of the stack (pull latest, restart services)
- [ ] **Backup script** — generate a companion backup script (cron job for workspace + OnGarde audit DB)

### Script Quality
- [ ] **Dry-run mode** — `--dry-run` flag in the script that prints all actions without executing
- [ ] **Idempotency** — full idempotent script (safe to re-run on an existing install; detects already-installed components)
- [ ] **Rollback support** — checkpoint + rollback mechanism if a phase fails mid-install
- [ ] **Script versioning** — embed a `SCRIPT_VERSION` var for support purposes
- [ ] **Structured logging** — `--json-log` flag emitting JSON events for each phase; enables progress visualization in a future web UI

---

## 🟢 Nice to Have / Future

### Messaging & Channels
- [ ] **Telegram bot setup section** — enter bot token, get OpenClaw connected to Telegram at install time
- [ ] **Signal gateway setup** — optional signal-cli config
- [ ] **Discord bot section** — bot token + server ID → OpenClaw connected to Discord

### Infrastructure
- [ ] **Terraform / cloud-init integration** — generate a `cloud-init.yaml` or Terraform `user_data` block; user pastes into DigitalOcean/Hetzner/Vultr droplet creation form for true zero-touch provisioning
- [ ] **SSH execute mode** — browser connects to a backend service that SSHes in and runs the script in real-time with live terminal output (Option B from original design)
- [ ] **Multi-node setup** — generate scripts for a cluster: separate OnGarde proxy node, OpenClaw node, shared workspace via NFS or Syncthing

### UI & Product
- [ ] **Script profiles** — saved named configs (e.g., "Minimal: OpenClaw + OnGarde only", "Full BMad stack", "Dev machine")
- [ ] **Step-by-step wizard mode** — alternative UX: wizard instead of one-screen form; better for onboarding non-technical users
- [ ] **Inline script documentation toggle** — toggle between "clean script" and "heavily commented" output
- [ ] **Dark/light theme toggle**
- [ ] **Add to ongarde.io/resources** — embed the generator on the website as an Astro island component
- [ ] **Analytics** — track which agents are most commonly selected, which OS, which options (privacy-safe, no PII)

### Integrations
- [ ] **OnGarde allowlist pre-seed** — UI section to add known-safe patterns to the allowlist at install time
- [ ] **Model selection per provider** — specify default model (e.g., `claude-sonnet-4-6`, `gpt-4o`) in OpenClaw config
- [ ] **Ollama local model support** — toggle to add Ollama install + configure OnGarde to proxy to it

---

## 🐛 Known Issues / Tech Debt

- [ ] SSH hardening disables password auth but doesn't verify key is in authorized_keys first — script warns, but should gate the step on key verification
- [ ] File Browser password hash is generated at runtime; if `filebrowser hash` command fails, uses a placeholder — needs better error handling
- [ ] BMad clone uses `--depth=1` (shallow); some workflows may need full history — add option
- [ ] `openssl rand` used for gateway token; if OpenSSL not installed (edge case), token generation fails silently
- [ ] Extra provider URLs default to placeholder comments — user needs to manually configure upstream URL for non-Anthropic/OpenAI providers

---

## ✅ Completed

- [x] **POC v1** — single HTML file, client-side generation, Ubuntu 22.04/24.04, BMad agents with name/role assignment, OnGarde from GitHub main, all 5 config sections, copy + download
