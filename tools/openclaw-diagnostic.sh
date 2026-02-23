#!/bin/bash
#
# OnGarde OpenClaw Diagnostic Tool
# 
# This script probes an OpenClaw installation to gather comprehensive
# information needed to build the OnGarde integration.
#
# Usage: bash openclaw-diagnostic.sh > openclaw-report.txt
#

set -e

REPORT_VERSION="1.0.0"
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")

echo "============================================================"
echo "OnGarde OpenClaw Diagnostic Report"
echo "Version: $REPORT_VERSION"
echo "Timestamp: $TIMESTAMP"
echo "============================================================"
echo ""

# Function to print section headers
section() {
    echo ""
    echo "============================================================"
    echo "$1"
    echo "============================================================"
    echo ""
}

# Function to safely display file contents (with sanitization)
safe_cat() {
    local file="$1"
    local label="${2:-$file}"
    
    if [ -f "$file" ]; then
        echo "--- $label ---"
        # Sanitize sensitive data
        cat "$file" | \
            sed 's/\("apiKey"\|"token"\|"password"\|"secret"\):\s*"[^"]*"/\1: "***REDACTED***/g' | \
            sed 's/\(API_KEY\|TOKEN\|PASSWORD\|SECRET\)=[^ ]*/\1=***REDACTED***/g' | \
            head -n 500  # Limit to 500 lines
        echo ""
    else
        echo "--- $label ---"
        echo "File not found: $file"
        echo ""
    fi
}

# Function to run command and show output
run_cmd() {
    local cmd="$1"
    local label="${2:-$cmd}"
    
    echo "--- $label ---"
    if eval "$cmd" 2>&1; then
        echo ""
    else
        echo "Command failed or not available"
        echo ""
    fi
}

#
# SECTION 1: System Information
#
section "1. System Information"

run_cmd "uname -a" "Kernel & OS"
run_cmd "cat /etc/os-release" "OS Release Info"
run_cmd "whoami" "Current User"
run_cmd "pwd" "Current Directory"
run_cmd "hostname" "Hostname"
run_cmd "uptime" "System Uptime"

#
# SECTION 2: OpenClaw Installation Detection
#
section "2. OpenClaw Installation Detection"

echo "--- Searching for OpenClaw installations ---"

# Check common installation locations
OPENCLAW_LOCATIONS=(
    "$(which openclaw 2>/dev/null || echo '')"
    "/usr/local/bin/openclaw"
    "/usr/bin/openclaw"
    "$HOME/.local/bin/openclaw"
    "$(npm root -g 2>/dev/null)/openclaw/bin/openclaw.js"
)

OPENCLAW_BIN=""
for loc in "${OPENCLAW_LOCATIONS[@]}"; do
    if [ -n "$loc" ] && [ -f "$loc" ]; then
        echo "Found: $loc"
        OPENCLAW_BIN="$loc"
        break
    fi
done

if [ -n "$OPENCLAW_BIN" ]; then
    echo ""
    echo "OpenClaw binary: $OPENCLAW_BIN"
    run_cmd "$OPENCLAW_BIN --version" "OpenClaw Version"
else
    echo "OpenClaw binary not found in standard locations"
fi

echo ""

# Check for OpenClaw data directory
OPENCLAW_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
echo "OpenClaw data directory: $OPENCLAW_DIR"

if [ -d "$OPENCLAW_DIR" ]; then
    echo "✓ Directory exists"
    run_cmd "ls -la $OPENCLAW_DIR" "Directory Contents"
else
    echo "✗ Directory not found"
fi

#
# SECTION 3: OpenClaw Configuration
#
section "3. OpenClaw Configuration"

CONFIG_FILE="$OPENCLAW_DIR/openclaw.json"
safe_cat "$CONFIG_FILE" "openclaw.json (sanitized)"

# Check for other config files
for config in "$OPENCLAW_DIR/openclaw.json5" "$OPENCLAW_DIR/config.json"; do
    if [ -f "$config" ]; then
        safe_cat "$config" "$(basename $config) (sanitized)"
    fi
done

#
# SECTION 4: Environment Variables
#
section "4. Environment Variables"

echo "--- OpenClaw-related environment variables ---"
env | grep -i "openclaw\|openai\|anthropic\|api_key\|token" | \
    sed 's/=.*/=***REDACTED***/g' || echo "None found"
echo ""

#
# SECTION 5: Running Processes
#
section "5. Running Processes"

run_cmd "ps aux | grep -i openclaw | grep -v grep" "OpenClaw Processes"
run_cmd "ps aux | grep -i node | grep -v grep" "Node.js Processes"

#
# SECTION 6: Network Configuration
#
section "6. Network Configuration"

run_cmd "netstat -tulpn 2>/dev/null | grep -i listen || ss -tulpn | grep -i listen" "Listening Ports"

echo ""
echo "--- Checking for OpenClaw Gateway port (default 18789) ---"
if netstat -tulpn 2>/dev/null | grep -q ":18789" || ss -tulpn 2>/dev/null | grep -q ":18789"; then
    echo "✓ Port 18789 is listening"
    netstat -tulpn 2>/dev/null | grep ":18789" || ss -tulpn | grep ":18789"
else
    echo "✗ Port 18789 not found (Gateway may be on different port or not running)"
fi
echo ""

#
# SECTION 7: Node.js & npm Environment
#
section "7. Node.js & npm Environment"

run_cmd "node --version" "Node.js Version"
run_cmd "npm --version" "npm Version"
run_cmd "npm root -g" "Global npm Modules Path"
run_cmd "npm list -g --depth=0 2>/dev/null | grep openclaw" "Global OpenClaw Packages"

#
# SECTION 8: Skills Directory
#
section "8. Skills Directory"

SKILLS_DIR="$OPENCLAW_DIR/skills"
if [ -d "$SKILLS_DIR" ]; then
    echo "✓ Skills directory found: $SKILLS_DIR"
    echo ""
    run_cmd "find $SKILLS_DIR -name 'SKILL.md' -type f" "Installed Skills (SKILL.md files)"
    echo ""
    
    # Show a sample skill structure
    SAMPLE_SKILL=$(find "$SKILLS_DIR" -name 'SKILL.md' -type f | head -n 1)
    if [ -n "$SAMPLE_SKILL" ]; then
        SKILL_DIR=$(dirname "$SAMPLE_SKILL")
        echo "--- Sample skill structure: $SKILL_DIR ---"
        ls -la "$SKILL_DIR"
        echo ""
        safe_cat "$SAMPLE_SKILL" "Sample SKILL.md"
    fi
else
    echo "✗ Skills directory not found"
fi

#
# SECTION 9: Extensions/Plugins
#
section "9. Extensions & Plugins"

EXTENSIONS_DIR="$OPENCLAW_DIR/extensions"
if [ -d "$EXTENSIONS_DIR" ]; then
    echo "✓ Extensions directory found: $EXTENSIONS_DIR"
    echo ""
    run_cmd "ls -la $EXTENSIONS_DIR" "Installed Extensions"
    
    # Check for plugin manifests
    echo ""
    run_cmd "find $EXTENSIONS_DIR -name 'openclaw.plugin.json' -type f" "Plugin Manifests"
else
    echo "✗ Extensions directory not found"
fi

#
# SECTION 10: Credentials & Auth
#
section "10. Credentials & Auth (Sanitized)"

CREDS_DIR="$OPENCLAW_DIR/credentials"
if [ -d "$CREDS_DIR" ]; then
    echo "✓ Credentials directory found: $CREDS_DIR"
    echo ""
    echo "--- Structure (files only, no contents) ---"
    find "$CREDS_DIR" -type f | sed "s|$CREDS_DIR/||"
    echo ""
else
    echo "✗ Credentials directory not found"
fi

#
# SECTION 11: Agents Configuration
#
section "11. Agents Configuration"

AGENTS_DIR="$OPENCLAW_DIR/agents"
if [ -d "$AGENTS_DIR" ]; then
    echo "✓ Agents directory found: $AGENTS_DIR"
    echo ""
    run_cmd "ls -la $AGENTS_DIR" "Agent Directories"
    
    # Check for agent configs
    for agent_dir in "$AGENTS_DIR"/*; do
        if [ -d "$agent_dir" ]; then
            agent_name=$(basename "$agent_dir")
            echo ""
            echo "--- Agent: $agent_name ---"
            
            if [ -f "$agent_dir/agent/auth-profiles.json" ]; then
                echo "Auth profiles found (not displaying for security)"
            fi
            
            if [ -d "$agent_dir/sessions" ]; then
                session_count=$(find "$agent_dir/sessions" -name '*.jsonl' | wc -l)
                echo "Session files: $session_count"
            fi
        fi
    done
else
    echo "✗ Agents directory not found"
fi

#
# SECTION 12: Gateway Logs
#
section "12. Gateway Logs"

echo "--- Searching for gateway logs ---"

LOG_LOCATIONS=(
    "$OPENCLAW_DIR/logs"
    "/tmp/openclaw"
    "/var/log/openclaw"
)

for log_dir in "${LOG_LOCATIONS[@]}"; do
    if [ -d "$log_dir" ]; then
        echo ""
        echo "Log directory found: $log_dir"
        run_cmd "ls -lht $log_dir | head -n 10" "Recent Log Files"
        
        # Show last 50 lines of most recent log
        LATEST_LOG=$(ls -t "$log_dir"/*.log 2>/dev/null | head -n 1)
        if [ -n "$LATEST_LOG" ]; then
            echo ""
            echo "--- Last 50 lines of: $(basename $LATEST_LOG) ---"
            tail -n 50 "$LATEST_LOG" | \
                sed 's/\("apiKey"\|"token"\|"password"\):\s*"[^"]*"/\1: "***REDACTED***/g'
            echo ""
        fi
    fi
done

#
# SECTION 13: HTTP/API Interception Points
#
section "13. HTTP/API Interception Analysis"

echo "--- Analyzing potential interception points ---"
echo ""

# Check if openclaw.json has models.providers
if [ -f "$CONFIG_FILE" ]; then
    echo "Checking for custom model providers in config..."
    if grep -q '"providers"' "$CONFIG_FILE" 2>/dev/null; then
        echo "✓ Found models.providers section"
        grep -A 20 '"providers"' "$CONFIG_FILE" | head -n 20
    else
        echo "✗ No custom providers configured"
    fi
    echo ""
    
    # Check for baseUrl configurations
    if grep -q '"baseUrl"' "$CONFIG_FILE" 2>/dev/null; then
        echo "✓ Found baseUrl configurations:"
        grep '"baseUrl"' "$CONFIG_FILE"
    else
        echo "✗ No baseUrl configurations found"
    fi
fi

echo ""

#
# SECTION 14: Docker/Container Detection
#
section "14. Docker/Container Detection"

run_cmd "docker --version" "Docker Version"

if command -v docker &> /dev/null; then
    run_cmd "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'" "Running Containers"
    
    # Check for OpenClaw containers
    if docker ps | grep -q openclaw; then
        echo ""
        echo "--- OpenClaw container detected ---"
        docker ps | grep openclaw
    fi
fi

#
# SECTION 15: Network Routing & Gateways
#
section "15. Network Routing Analysis"

echo "--- Checking for VM/VPS gateway routing ---"
echo ""

# Check for common proxy/gateway configurations
if [ -f "/etc/nginx/nginx.conf" ]; then
    echo "✓ Nginx found"
    run_cmd "nginx -v" "Nginx Version"
fi

if [ -f "/etc/caddy/Caddyfile" ]; then
    echo "✓ Caddy found"
fi

# Check for proxy environment variables
echo ""
echo "--- Proxy environment variables ---"
env | grep -i "proxy" || echo "None found"
echo ""

# Check routing table
run_cmd "ip route 2>/dev/null || route -n" "Routing Table"

#
# SECTION 16: OnGarde Integration Readiness Check
#
section "16. OnGarde Integration Readiness"

echo "--- Checking prerequisites for OnGarde integration ---"
echo ""

# Check Node.js version
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version | sed 's/v//')
    NODE_MAJOR=$(echo $NODE_VERSION | cut -d. -f1)
    
    if [ "$NODE_MAJOR" -ge 18 ]; then
        echo "✓ Node.js $NODE_VERSION (>= 18 required)"
    else
        echo "✗ Node.js $NODE_VERSION (< 18, upgrade needed)"
    fi
else
    echo "✗ Node.js not found"
fi

# Check npm
if command -v npm &> /dev/null; then
    echo "✓ npm $(npm --version)"
else
    echo "✗ npm not found"
fi

# Check Python
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    echo "✓ Python $PYTHON_VERSION"
else
    echo "✗ Python not found"
fi

# Check pip
if command -v pip3 &> /dev/null; then
    echo "✓ pip3 $(pip3 --version | awk '{print $2}')"
else
    echo "✗ pip3 not found"
fi

# Check port availability for OnGarde proxy
echo ""
echo "--- Checking port availability for OnGarde (default: 8000) ---"
if netstat -tulpn 2>/dev/null | grep -q ":8000" || ss -tulpn 2>/dev/null | grep -q ":8000"; then
    echo "✗ Port 8000 already in use (will need alternative)"
    netstat -tulpn 2>/dev/null | grep ":8000" || ss -tulpn | grep ":8000"
else
    echo "✓ Port 8000 available"
fi

#
# SECTION 17: Test API Call Flow
#
section "17. Test API Call Flow (if Gateway is running)"

if netstat -tulpn 2>/dev/null | grep -q ":18789" || ss -tulpn 2>/dev/null | grep -q ":18789"; then
    echo "Gateway appears to be running, attempting health check..."
    echo ""
    
    # Try to connect to gateway
    if command -v curl &> /dev/null; then
        run_cmd "curl -s http://localhost:18789/ 2>&1 | head -n 20" "Gateway Root Response"
    else
        echo "curl not available for testing"
    fi
else
    echo "Gateway not currently running (port 18789 not listening)"
fi

#
# SECTION 18: Recommendations
#
section "18. OnGarde Integration Recommendations"

echo "Based on this diagnostic, here are the integration recommendations:"
echo ""

if [ -f "$CONFIG_FILE" ]; then
    echo "✓ OpenClaw configuration found"
    echo "  → OnGarde can modify models.providers section"
    echo ""
fi

if [ -d "$OPENCLAW_DIR" ]; then
    echo "✓ OpenClaw data directory accessible"
    echo "  → OnGarde can create backups"
    echo ""
fi

if grep -q '"baseUrl"' "$CONFIG_FILE" 2>/dev/null; then
    echo "✓ baseUrl configuration already in use"
    echo "  → OnGarde integration pattern validated"
    echo ""
else
    echo "ℹ No existing baseUrl configurations"
    echo "  → OnGarde will be first to use this pattern"
    echo ""
fi

echo "Recommended OnGarde proxy port: 8000"
echo "Recommended integration method: models.providers baseUrl override"
echo ""

#
# FINAL SUMMARY
#
section "End of Diagnostic Report"

echo "Report completed at: $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
echo ""
echo "Next steps:"
echo "1. Review this report"
echo "2. Share with OnGarde development team"
echo "3. Identify any missing information"
echo "4. Proceed with OnGarde CLI development"
echo ""
echo "============================================================"
