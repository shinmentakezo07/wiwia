#!/usr/bin/env bash
# start.sh — install web deps (npm) and run the wiwi backend proxy with
# auto-reload: edit any Python file under wiwi/ and the server restarts
# automatically — no manual restart needed.
# Kills any old process on the proxy port before starting.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROXY_PORT="${WIWI_PORT:-4000}"
WEB_DIR="$SCRIPT_DIR/web"
VENV_BIN="$SCRIPT_DIR/.venv/bin"
WIWI_BIN="${WIWI_BIN:-$VENV_BIN/wiwi}"
RELOAD="${WIWI_RELOAD:-1}"          # set WIWI_RELOAD=0 to disable
RELOAD_DIRS="${WIWI_RELOAD_DIRS:-wiwi}"  # comma-separated, e.g. "wiwi,tests"

echo "==> Installing web dependencies (npm) in $WEB_DIR ..."
cd "$WEB_DIR"
npm install
cd "$SCRIPT_DIR"

echo "==> Checking for processes on port $PROXY_PORT ..."
PIDS="$(lsof -t -i:"$PROXY_PORT" 2>/dev/null || true)"
if [ -n "$PIDS" ]; then
    echo "    Found process(es) on port $PROXY_PORT (pid: $(echo "$PIDS" | tr '\n' ' ')) — killing ..."
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    sleep 1
    # force-kill any survivors
    PIDS2="$(lsof -t -i:"$PROXY_PORT" 2>/dev/null || true)"
    if [ -n "$PIDS2" ]; then
        echo "    Still alive — force killing (pid: $(echo "$PIDS2" | tr '\n' ' ')) ..."
        # shellcheck disable=SC2086
        kill -9 $PIDS2 2>/dev/null || true
        sleep 1
    fi
    echo "    Port $PROXY_PORT is now free."
else
    echo "    Port $PROXY_PORT is already free."
fi

# Build the command args
CMD_ARGS=(--config "$SCRIPT_DIR/wiwi.yaml" --port "$PROXY_PORT")
if [ "$RELOAD" = "1" ]; then
    CMD_ARGS+=(--reload)
    # Convert comma-separated WIWI_RELOAD_DIRS into repeated --reload-dir flags
    IFS=',' read -ra DIRS <<< "$RELOAD_DIRS"
    for d in "${DIRS[@]}"; do
        CMD_ARGS+=(--reload-dir "$d")
    done
    echo "==> Starting wiwi proxy on port $PROXY_PORT (auto-reload ON, watching: $RELOAD_DIRS) ..."
else
    echo "==> Starting wiwi proxy on port $PROXY_PORT (reload OFF) ..."
fi

if [ -x "$WIWI_BIN" ]; then
    exec "$WIWI_BIN" "${CMD_ARGS[@]}"
elif command -v wiwi >/dev/null 2>&1; then
    exec wiwi "${CMD_ARGS[@]}"
else
    echo "ERROR: 'wiwi' not found. Activate the venv or install the package: uv pip install -e .[dev]"
    exit 1
fi
