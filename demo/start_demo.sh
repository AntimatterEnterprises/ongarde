#!/usr/bin/env bash
# start_demo.sh — OnGarde Live Demo Launcher
#
# Starts:
#   1. Mock LLM upstream   (port 4243)
#   2. OnGarde proxy       (port 4242)
#
# Then prints instructions for:
#   - Opening the dashboard in your browser
#   - Running the demo chat client

set -e
cd "$(dirname "$0")/.."  # Run from ongarde/ root

RESET='\033[0m'
BOLD='\033[1m'
GREEN='\033[92m'
CYAN='\033[96m'
YELLOW='\033[93m'
RED='\033[91m'
GRAY='\033[90m'

ONGARDE_PORT=4242
MOCK_PORT=4243
LOG_DIR="demo/logs"
mkdir -p "$LOG_DIR"

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║          🤺  OnGarde Demo Launcher  🤺               ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""

# ── Kill any existing processes on our ports ───────────────────────────────
kill_port() {
    local port=$1
    local pid
    pid=$(lsof -ti tcp:$port 2>/dev/null || true)
    if [ -n "$pid" ]; then
        echo -e "${YELLOW}  Stopping existing process on port $port (PID $pid)...${RESET}"
        kill "$pid" 2>/dev/null || true
        sleep 1
    fi
}

echo -e "${BOLD}[1/3] Cleaning up ports...${RESET}"
kill_port $MOCK_PORT
kill_port $ONGARDE_PORT
echo -e "${GREEN}  ✓ Ports clear${RESET}"

# ── Start mock upstream ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/3] Starting Mock LLM upstream (port $MOCK_PORT)...${RESET}"
python3 demo/mock_upstream.py > "$LOG_DIR/mock_upstream.log" 2>&1 &
MOCK_PID=$!
echo $MOCK_PID > "$LOG_DIR/mock_upstream.pid"

# Wait for mock to be ready
for i in $(seq 1 10); do
    if curl -sf "http://127.0.0.1:$MOCK_PORT/health" > /dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Mock LLM ready (PID $MOCK_PID)${RESET}"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "${RED}  ✗ Mock LLM failed to start. Check: $LOG_DIR/mock_upstream.log${RESET}"
        exit 1
    fi
    sleep 0.5
done

# ── Start OnGarde ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[3/3] Starting OnGarde proxy (port $ONGARDE_PORT)...${RESET}"
JSON_LOGS=false LOG_LEVEL=INFO python3 -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port $ONGARDE_PORT \
    --limit-concurrency 100 \
    --backlog 50 \
    --timeout-keep-alive 5 \
    > "$LOG_DIR/ongarde.log" 2>&1 &
ONGARDE_PID=$!
echo $ONGARDE_PID > "$LOG_DIR/ongarde.pid"

# Wait for OnGarde to be ready
echo -e "${GRAY}  (Waiting for scanner calibration — may take 10-20s first run...)${RESET}"
for i in $(seq 1 40); do
    if curl -sf "http://127.0.0.1:$ONGARDE_PORT/health" > /dev/null 2>&1; then
        STATUS=$(curl -sf "http://127.0.0.1:$ONGARDE_PORT/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
        echo -e "${GREEN}  ✓ OnGarde ready (PID $ONGARDE_PID, status: $STATUS)${RESET}"
        break
    fi
    if [ $i -eq 40 ]; then
        echo -e "${RED}  ✗ OnGarde failed to start. Check: $LOG_DIR/ongarde.log${RESET}"
        echo -e "${GRAY}  Last log lines:${RESET}"
        tail -20 "$LOG_DIR/ongarde.log"
        exit 1
    fi
    sleep 1
done

# ── Print instructions ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║              🎉 OnGarde is running! 🎉               ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "${BOLD}  📊 Dashboard (open in your browser):${RESET}"
echo -e "     ${CYAN}http://127.0.0.1:$ONGARDE_PORT/dashboard${RESET}"
echo ""
echo -e "${BOLD}  💬 Start the demo chat (in a new terminal):${RESET}"
echo -e "     ${CYAN}cd ongarde && python3 demo/demo_chat.py${RESET}"
echo ""
echo -e "${BOLD}  🎬 Run auto-demo (fires 10 preset scenarios):${RESET}"
echo -e "     ${CYAN}cd ongarde && python3 demo/demo_chat.py --auto${RESET}"
echo ""
echo -e "${BOLD}  🔍 Watch live logs:${RESET}"
echo -e "     ${CYAN}tail -f demo/logs/ongarde.log${RESET}"
echo ""
echo -e "${BOLD}  🛑 Stop everything:${RESET}"
echo -e "     ${CYAN}kill \$(cat demo/logs/ongarde.pid demo/logs/mock_upstream.pid)${RESET}"
echo ""
echo -e "${GRAY}  Logs: $LOG_DIR/ongarde.log | $LOG_DIR/mock_upstream.log${RESET}"
echo ""
echo -e "${YELLOW}  Tip: Open the dashboard FIRST, then run the chat.${RESET}"
echo -e "${YELLOW}  Watch events appear in real time as you type!${RESET}"
echo ""
