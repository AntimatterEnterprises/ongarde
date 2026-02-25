# OnGarde Installation Guide

**Target:** Ubuntu 24.04 LTS — DigitalOcean Droplet, running as root.

---

## Phase 1: Server Setup & Hardening

### 1.1 Create the Droplet

- **Image:** Ubuntu 24.04 LTS x64
- **Plan:** 2 vCPU / 4 GB RAM minimum (4 vCPU / 8 GB recommended)
- **Auth:** SSH key recommended

SSH in as root:

```bash
ssh root@YOUR_SERVER_IP
```

### 1.2 System Update

```bash
apt update && apt upgrade -y
```

### 1.3 Set Hostname

```bash
hostnamectl set-hostname ongarde-prod
echo "127.0.1.1	ongarde-prod" >> /etc/hosts
```

### 1.4 Set Timezone

```bash
timedatectl set-timezone UTC
```

### 1.5 Configure Firewall (UFW)

Allow SSH before enabling — wrong order will lock you out.

```bash
apt install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 18789/tcp
ufw allow 8080/tcp
ufw --force enable
ufw status verbose
```

### 1.6 Install fail2ban

Protects SSH from brute-force attempts.

```bash
apt install -y fail2ban
```

```bash
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port    = 22
filter  = sshd
logpath = /var/log/auth.log
EOF
```

```bash
systemctl enable --now fail2ban
fail2ban-client status sshd
```

### 1.7 Enable Automatic Security Updates

```bash
apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades
```

Select **Yes** when prompted.

---

## Phase 2: OpenClaw & FileBrowser

### 2.1 Install Node.js LTS

```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
apt install -y nodejs
```

### 2.2 Install OpenClaw

```bash
npm install -g openclaw
openclaw --version
```

### 2.3 Configure OpenClaw

```bash
mkdir -p ~/.openclaw
```

Generate a gateway auth token:

```bash
openssl rand -hex 24
```

Create the config — paste your generated token and API keys:

```bash
cat > ~/.openclaw/openclaw.json << 'EOF'
{
  "gateway": {
    "port": 18789,
    "mode": "local",
    "bind": "lan",
    "auth": {
      "mode": "token",
      "token": "PASTE_GENERATED_TOKEN_HERE"
    }
  },
  "models": {
    "providers": {
      "anthropic": {
        "apiKey": "YOUR_ANTHROPIC_KEY",
        "baseUrl": "http://127.0.0.1:4242"
      },
      "openai": {
        "apiKey": "YOUR_OPENAI_KEY",
        "baseUrl": "http://127.0.0.1:4242"
      }
    }
  }
}
EOF
```

### 2.4 Create OpenClaw Service

```bash
cat > /etc/systemd/system/openclaw.service << 'EOF'
[Unit]
Description=OpenClaw Gateway
After=network.target ongarde.service
Wants=ongarde.service

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/openclaw gateway run --bind lan --port 18789
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

```bash
systemctl daemon-reload
systemctl enable openclaw
```

### 2.5 Install FileBrowser

FileBrowser provides a web UI for the OpenClaw workspace.

```bash
curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash
```

Initialize the database (run once):

```bash
filebrowser config init \
  --address 0.0.0.0 \
  --port 8080 \
  --root /root/.openclaw/workspace \
  --database /root/.filebrowser.db

filebrowser users add admin admin --perm.admin --database /root/.filebrowser.db
```

### 2.6 Create FileBrowser Service

```bash
cat > /etc/systemd/system/filebrowser.service << 'EOF'
[Unit]
Description=FileBrowser
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/filebrowser \
  --address 0.0.0.0 \
  --port 8080 \
  --root /root/.openclaw/workspace \
  --database /root/.filebrowser.db
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

```bash
systemctl daemon-reload
systemctl enable --now filebrowser
systemctl status filebrowser
```

Default login: **admin / admin** — change this after first login via Settings → Profile.

---

## Phase 3: BMad Agents

### 3.1 Clone BMad into Workspace

```bash
mkdir -p ~/.openclaw/workspace
cd ~/.openclaw/workspace
git clone https://github.com/OpenClawRocks/bmad-openclaw.git
```

### 3.2 Create Agent Roster

```bash
cat > ~/.openclaw/workspace/AGENTS.md << 'EOF'
# AGENTS.md

## Agent Roster

| Handle  | Role               | BMad File                              |
|---------|--------------------|----------------------------------------|
| Mary    | Business Analyst   | bmad-openclaw/agents/analyst.md        |
| John    | Product Manager    | bmad-openclaw/agents/product-manager.md |
| Winston | Architect          | bmad-openclaw/agents/architect.md      |
| Amelia  | Developer          | bmad-openclaw/agents/developer.md      |
| Quinn   | QA Engineer        | bmad-openclaw/agents/qa-engineer.md    |
| Bob     | Scrum Master       | bmad-openclaw/agents/scrum-master.md   |
EOF
```

Verify agent files are in place:

```bash
ls ~/.openclaw/workspace/bmad-openclaw/agents/
```

---

## Phase 4: OnGarde

### 4.1 Install Python Dependencies

```bash
apt install -y python3.12-venv python3.12-dev build-essential
```

### 4.2 Clone OnGarde

```bash
git clone https://github.com/AntimatterEnterprises/ongarde.git /opt/ongarde
cd /opt/ongarde
```

### 4.3 Create Virtual Environment & Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[full]"
```

### 4.4 Download Language Model

Required for OnGarde's PII scanner.

```bash
python -m spacy download en_core_web_sm
```

### 4.5 Create OnGarde Config

```bash
mkdir -p ~/.ongarde
```

```bash
cat > ~/.ongarde/config.yaml << 'EOF'
version: 1

upstream:
  openai: "https://api.openai.com"
  anthropic: "https://api.anthropic.com"

proxy:
  host: "127.0.0.1"
  port: 4242

scanner:
  mode: "full"

audit:
  retention_days: 90
  path: "~/.ongarde/audit.db"

strict_mode: false
EOF
```

### 4.6 Create OnGarde Service

```bash
cat > /etc/systemd/system/ongarde.service << 'EOF'
[Unit]
Description=OnGarde — AI Security Proxy
After=network.target
Before=openclaw.service

[Service]
Type=simple
User=root
Environment=HOME=/root
WorkingDirectory=/opt/ongarde
ExecStart=/opt/ongarde/.venv/bin/ongarde
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

```bash
systemctl daemon-reload
systemctl enable --now ongarde
systemctl status ongarde
```

### 4.7 Start OpenClaw

OnGarde must be running before OpenClaw starts.

```bash
systemctl start openclaw
systemctl status openclaw
```

### 4.8 Verify Installation

```bash
# OnGarde health check
curl -s http://localhost:4242/health

# All three services active
systemctl is-active ongarde openclaw filebrowser
```

**Dashboard access** requires an SSH tunnel — the dashboard only accepts loopback connections.

On your local machine:

```bash
ssh -L 4242:127.0.0.1:4242 root@YOUR_SERVER_IP -N
```

Then open: **http://localhost:4242/dashboard**

**FileBrowser** is available directly at: **http://YOUR_SERVER_IP:8080**

---

## Service Reference

| Service     | Start                        | Stop                        | Logs                          |
|-------------|------------------------------|-----------------------------|-------------------------------|
| OnGarde     | `systemctl start ongarde`    | `systemctl stop ongarde`    | `journalctl -u ongarde -f`    |
| OpenClaw    | `systemctl start openclaw`   | `systemctl stop openclaw`   | `journalctl -u openclaw -f`   |
| FileBrowser | `systemctl start filebrowser`| `systemctl stop filebrowser`| `journalctl -u filebrowser -f`|

## Port Reference

| Service        | Port  | Access                              |
|----------------|-------|-------------------------------------|
| SSH            | 22    |                                     |
| OpenClaw       | 18789 | http://YOUR_SERVER_IP:18789         |
| OnGarde        | 4242  | SSH tunnel required for dashboard   |
| FileBrowser    | 8080  | http://YOUR_SERVER_IP:8080          |
