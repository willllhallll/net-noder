#!/usr/bin/env bash
# Launch the netnoder API, then the web dev server, in the background.
# PIDs and logs are kept under .run/ so scripts/stop.sh can shut them down later.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

API_PID_FILE="$RUN_DIR/api.pid"
WEB_PID_FILE="$RUN_DIR/web.pid"
API_LOG="$RUN_DIR/api.log"
WEB_LOG="$RUN_DIR/web.log"

API_URL="http://localhost:${NETNODER_PORT:-8000}"
WEB_URL="http://localhost:5173"

# True if the PID in $1 names a live process.
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# Start a command in its own session (so the whole process tree can be signalled
# as a group later) unless it is already running. $1=name $2=pidfile $3=logfile, rest=cmd
start() {
  local name="$1" pidfile="$2" logfile="$3"; shift 3
  if alive "$pidfile"; then
    echo "• $name already running (pid $(cat "$pidfile"))"
    return
  fi
  echo "• starting $name…"
  setsid "$@" >"$logfile" 2>&1 < /dev/null &
  echo $! > "$pidfile"
}

# Poll a URL until it answers or we give up. $1=label $2=url $3=logfile
wait_ready() {
  local label="$1" url="$2" logfile="$3" i
  for i in $(seq 1 60); do
    if curl -sf -o /dev/null "$url"; then
      echo "✓ $label ready at $url"
      return 0
    fi
    sleep 0.5
  done
  echo "✗ $label did not become ready at $url — last log lines:"
  tail -n 15 "$logfile" 2>/dev/null | sed 's/^/    /'
  return 1
}

# API: prefer the venv binary if present, else whatever is on PATH.
API_BIN="$ROOT/.venv/bin/netnoder-api"
[ -x "$API_BIN" ] || API_BIN="netnoder-api"

start "API"       "$API_PID_FILE" "$API_LOG" "$API_BIN"
start "web (dev)" "$WEB_PID_FILE" "$WEB_LOG" npm --prefix "$ROOT/web" run dev

rc=0
wait_ready "API" "$API_URL/docs" "$API_LOG" || rc=1
wait_ready "web" "$WEB_URL"      "$WEB_LOG" || rc=1

echo
echo "API : $API_URL  (OpenAPI at $API_URL/docs)  log: .run/api.log"
echo "Web : $WEB_URL  (proxies /api → API)         log: .run/web.log"
echo "Stop with: /kill   (or bash scripts/stop.sh)"
exit $rc
