#!/usr/bin/env bash
# Gracefully stop the services started by scripts/dev.sh.
# Sends SIGTERM to each process group, waits, then escalates to SIGKILL.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"

# Gracefully stop one service. $1=label $2=pidfile
stop() {
  local label="$1" pidfile="$2" pid
  if [ ! -f "$pidfile" ]; then
    echo "• $label: no pidfile, nothing to stop"
    return
  fi
  pid="$(cat "$pidfile")"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "• $label: not running (stale pid $pid)"
    rm -f "$pidfile"
    return
  fi

  # Processes are started with setsid, so the pid is its own group leader;
  # the negative pid signals the whole tree (e.g. vite + esbuild children).
  echo "• stopping $label (pid $pid)…"
  kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null

  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "  …did not exit, sending SIGKILL"
    kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
  fi
  echo "✓ $label stopped"
  rm -f "$pidfile"
}

stop "web (dev)" "$RUN_DIR/web.pid"
stop "API"       "$RUN_DIR/api.pid"
