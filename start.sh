#!/usr/bin/env bash
# start.sh — install web deps, then run the wiwi backend AND the Vite dev
# server together with interleaved, prefixed logs. Ctrl-C stops both.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROXY_PORT="${WIWI_PORT:-4000}"
WEB_PORT="${WIWI_WEB_PORT:-5173}"
WEB_DIR="$SCRIPT_DIR/web"
VENV_BIN="$SCRIPT_DIR/.venv/bin"
WIWI_BIN="${WIWI_BIN:-$VENV_BIN/wiwi}"
RELOAD="${WIWI_RELOAD:-1}"          # set WIWI_RELOAD=0 to disable backend reload
RELOAD_DIRS="${WIWI_RELOAD_DIRS:-wiwi}"  # comma-separated, e.g. "wiwi,tests"

# --- helpers ----------------------------------------------------------------

kill_port() {
    local port="$1" pids=""
    # Find PIDs of processes listening on $port via ss.
    # mawk doesn't support match($0, /re/, arr) — use RSTART/RLENGTH instead.
    pids="$(ss -tlnp 2>/dev/null | awk -v p=":$port" '
        $0 ~ p {
            if (match($0, /pid=[0-9]+/)) {
                print substr($0, RSTART+4, RLENGTH-4)
            }
        }' | sort -u || true)"
    if [ -z "$pids" ]; then
        pids="$(pgrep -f "$port" 2>/dev/null || true)"
    fi
    if [ -n "$pids" ]; then
        echo "    killing pid(s): $(echo "$pids" | tr '\n' ' ')"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 1
        local survivors=""
        for p in $pids; do
            kill -0 "$p" 2>/dev/null && survivors="$survivors $p"
        done
        if [ -n "$survivors" ]; then
            echo "    force-killing survivors:$survivors"
            # shellcheck disable=SC2086
            kill -9 $survivors 2>/dev/null || true
            sleep 1
        fi
    fi
}

# Prefix every line from stdin with a label. Use sed for mawk compatibility
# and line-buffered output. The explicit fflush is for awk; sed is
# line-buffered by default on pipes.
prefixed() {
    local label="$1"
    sed -u "s/^/${label} | /"
}

# --- cleanup on exit --------------------------------------------------------

BACKEND_PID=""
WEB_PID=""

cleanup() {
    echo ""
    echo "==> Shutting down ..."
    for pid in "$BACKEND_PID" "$WEB_PID"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    sleep 2
    for pid in "$BACKEND_PID" "$WEB_PID"; do
        [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

# --- 1. install web deps -----------------------------------------------------

echo "==> Installing web dependencies (npm) in $WEB_DIR ..."
cd "$WEB_DIR"
npm install
cd "$SCRIPT_DIR"

# --- 2. free up the ports ----------------------------------------------------

echo "==> Freeing port $PROXY_PORT (backend) ..."
kill_port "$PROXY_PORT"

echo "==> Freeing port $WEB_PORT (web) ..."
kill_port "$WEB_PORT"

# --- 3. build backend command args -------------------------------------------

CMD_ARGS=(--config "$SCRIPT_DIR/wiwi.yaml" --port "$PROXY_PORT")
if [ "$RELOAD" = "1" ]; then
    CMD_ARGS+=(--reload)
    IFS=',' read -ra DIRS <<< "$RELOAD_DIRS"
    for d in "${DIRS[@]}"; do
        CMD_ARGS+=(--reload-dir "$d")
    done
fi

# --- 4. launch backend -------------------------------------------------------
# Use process substitution (> >(...)) for prefixing so $! captures the real
# process PID, not the pipeline subshell. This makes liveness checks and
# cleanup work on the actual server process.

echo "==> Starting wiwi backend on :$PROXY_PORT (reload: ${RELOAD}) ..."
if [ -x "$WIWI_BIN" ]; then
    "$WIWI_BIN" "${CMD_ARGS[@]}" > >(prefixed "backend") 2>&1 &
elif command -v wiwi >/dev/null 2>&1; then
    wiwi "${CMD_ARGS[@]}" > >(prefixed "backend") 2>&1 &
else
    echo "ERROR: 'wiwi' not found. Run: uv pip install -e .[dev]"
    exit 1
fi
BACKEND_PID=$!

# --- 5. launch web dev server ------------------------------------------------

echo "==> Starting Vite dev server (web) on :$WEB_PORT ..."
cd "$WEB_DIR"
npm run dev -- --port "$WEB_PORT" > >(prefixed "web") 2>&1 &
WEB_PID=$!
cd "$SCRIPT_DIR"

echo ""
echo "==> Both running. Backend: http://localhost:$PROXY_PORT  Web: http://localhost:$WEB_PORT"
echo "==> Press Ctrl-C to stop both."
echo ""

# --- 6. wait — if either dies, stop the other --------------------------------

while true; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "==> backend exited — stopping web ..."
        break
    fi
    if ! kill -0 "$WEB_PID" 2>/dev/null; then
        echo "==> web exited — stopping backend ..."
        break
    fi
    sleep 1
done
