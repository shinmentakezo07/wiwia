#!/usr/bin/env bash
# start.sh — install web deps, then run ONE wiwi backend (Go or Python, chosen
# below) AND the Vite dev server together with interleaved, prefixed logs.
# Ctrl-C stops both.
#
# Choose the backend with a positional arg or WIWI_BACKEND:
#   ./start.sh go            # wiwi-go/   (Go)
#   ./start.sh py            # wiwi/      (Python)
#   WIWI_BACKEND=go ./start.sh
# With neither set and an interactive terminal you are prompted; otherwise
# Python is the default. Only the chosen backend is launched.
#
# The Python backend ALWAYS runs this checkout's code: PYTHONPATH pins the repo
# first, so a stale site-packages install or an editable install pointing at a
# different checkout can never shadow it (that failure mode surfaced as
# "unsupported provider type 'opencode'" with new code in the tree).
# The Go backend is rebuilt from wiwi-go/ on every start (it has no hot reload).
# Frontend uses npm, matching web/package-lock.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"

PROXY_PORT="${WIWI_PORT:-4000}"
WEB_PORT="${WIWI_WEB_PORT:-5173}"
WEB_DIR="$SCRIPT_DIR/web"
# Ambient python3 has the runtime deps (never .venv/bin/python — see AGENTS.md).
PYBIN="${WIWI_PYTHON:-python3}"
# Explicit escape hatch only: path to a `wiwi` binary. Unset by default so
# the repo-pinned `python3 -m wiwi.main` below is always used.
WIWI_BIN="${WIWI_BIN:-}"
# Go backend locations.
GO_DIR="$SCRIPT_DIR/wiwi-go"
GO_BIN="${WIWI_GO_BIN:-$GO_DIR/bin/wiwi}"
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
RELOAD="${WIWI_RELOAD:-1}"          # set WIWI_RELOAD=0 to disable backend reload
RELOAD_DIRS="${WIWI_RELOAD_DIRS:-wiwi}"  # comma-separated, e.g. "wiwi,tests"

# --- backend selection ------------------------------------------------------
# A positional backend name (go|py|python|golang) or WIWI_BACKEND picks it.
# Prompt when neither is given and stdin is a terminal; otherwise default py.
BACKEND="${WIWI_BACKEND:-}"
if [ "$#" -ge 1 ]; then
    case "$1" in
        go|golang|GO|Go) BACKEND="go" ;;
        py|python|python3|PY|Py) BACKEND="py" ;;
        -*) : ;;  # leave flags for future use
        *) echo "ERROR: unknown backend '$1' (use: go | py)" >&2; exit 1 ;;
    esac
fi
if [ -z "$BACKEND" ]; then
    if [ -t 0 ]; then
        echo "==> Select backend:"
        echo "      1) python  (wiwi/)"
        echo "      2) go      (wiwi-go/)"
        read -r -p "    Choice [1/2, default 1]: " _choice
        case "$_choice" in
            2|go|g|GO|G) BACKEND="go" ;;
            *) BACKEND="py" ;;
        esac
    else
        BACKEND="py"
    fi
fi
case "$BACKEND" in
    go|golang) BACKEND="go" ;;
    py|python|python3) BACKEND="py" ;;
    *) echo "ERROR: unknown backend '$BACKEND' (use: go | py)" >&2; exit 1 ;;
esac
echo "==> Backend: $BACKEND"

# --- helpers ----------------------------------------------------------------

kill_port() {
    local port="$1" pids=""
    # Find PIDs of the processes actually LISTENING on $port. Only listeners
    # are freed — never match an arbitrary command line, or `pgrep -f 4000`
    # would kill an unrelated shell that merely mentions the port.
    # mawk doesn't support match($0, /re/, arr) — use RSTART/RLENGTH instead.
    pids="$(ss -tlnp 2>/dev/null | awk -v p=":$port " '
        $0 ~ p {
            if (match($0, /pid=[0-9]+/)) {
                print substr($0, RSTART+4, RLENGTH-4)
            }
        }' | sort -u || true)"
    if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
        # Fallback for hosts without ss: lsof reports listeners only (-sTCP:LISTEN).
        pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
    fi
    if [ -z "$pids" ] && command -v fuser >/dev/null 2>&1; then
        pids="$(fuser -n tcp "$port" 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u || true)"
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

# --- 1. install web deps (npm) -------------------------------------------------

# `npm ci` is the frozen-lockfile install: it installs strictly from
# web/package-lock.json and fails if package.json has drifted, which is
# exactly what `bun install --frozen-lockfile` used to do here.
echo "==> Installing web dependencies (npm) in $WEB_DIR ..."
(cd "$WEB_DIR" && npm ci)
DEV_CMD=(npm run dev -- --port "$WEB_PORT")

# --- 2. free up the ports ----------------------------------------------------

echo "==> Freeing port $PROXY_PORT (backend) ..."
kill_port "$PROXY_PORT"

echo "==> Freeing port $WEB_PORT (web) ..."
kill_port "$WEB_PORT"

# --- 3. backend preflight ----------------------------------------------------

if [ "$BACKEND" = "py" ]; then
    # --- 3a. verify the Python backend resolves to THIS checkout -------------
    echo "==> Verifying Python backend code ..."
    WIWI_SRC="$("$PYBIN" -c 'import wiwi, os; print(os.path.realpath(wiwi.__file__))')"
    WIWI_WANT="$(realpath "$SCRIPT_DIR/wiwi/__init__.py" 2>/dev/null || "$PYBIN" -c 'import os; print(os.path.realpath("wiwi/__init__.py"))')"
    if [ -z "$WIWI_SRC" ] || [ "$WIWI_SRC" != "$WIWI_WANT" ]; then
        echo "ERROR: 'import wiwi' resolves to '${WIWI_SRC:-<failed>}' instead of this checkout:"
        echo "       $WIWI_WANT"
        echo "Install deps into ambient python3 (uv pip install -e '.[dev]') and retry."
        exit 1
    fi
    echo "    backend code: $WIWI_SRC"
else
    # --- 3b. build the Go backend -------------------------------------------
    if ! command -v go >/dev/null 2>&1; then
        echo "ERROR: 'go' not found on PATH; install Go to run the Go backend." >&2
        exit 1
    fi
    echo "==> Building Go backend ($GO_DIR) ..."
    mkdir -p "$(dirname "$GO_BIN")"
    ( cd "$GO_DIR" && CGO_ENABLED=1 go build -o "$GO_BIN" ./cmd/wiwi )
    echo "    backend binary: $GO_BIN"
    if [ "$RELOAD" = "1" ]; then
        echo "    note: the Go backend has no hot reload; restart to pick up changes"
    fi
fi

# --- 4. redis for the response cache --------------------------------------
# Start a Redis container and point the gateway at it so the exact-match
# response cache is Redis-backed by default (survives restarts, shared across
# replicas). This uses a dedicated published-port container — NOT the compose
# `redis` service — because start.sh runs the backend on the host, and the
# compose service publishes no host port (it is only reachable over the
# compose network from the containerized wiwi).
#
# Redis is advisory for wiwi: if it is unreachable the cache degrades to a
# permanent miss and requests still succeed. So this section never fails the
# start — a missing docker daemon, a refused connection, or an image pull
# problem just logs a warning and lets the backend use the in-memory cache.
# Opt out with WIWI_DISABLE_REDIS=1 (e.g. to reuse an existing Redis).
REDIS_PORT="${WIWI_REDIS_PORT:-6379}"
REDIS_CONTAINER="wiwi-redis"

ensure_redis() {
    # Bring up $REDIS_CONTAINER and confirm it answers PING. Reuses a
    # container that already exists so repeated `./start.sh` runs are cheap.
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$REDIS_CONTAINER"; then
        docker start "$REDIS_CONTAINER" >/dev/null 2>&1 || true
    else
        docker run -d --name "$REDIS_CONTAINER" --restart unless-stopped \
            -p "127.0.0.1:${REDIS_PORT}:6379" redis:7-alpine \
            redis-server --appendonly no --maxmemory 256mb \
            --maxmemory-policy allkeys-lru >/dev/null 2>&1 || return 1
    fi
    # Bounded readiness wait (~10s): PING inside the container proves the
    # server is up without depending on the host port being bindable yet.
    for _ in $(seq 1 20); do
        docker exec "$REDIS_CONTAINER" redis-cli ping >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    return 1
}

if [ "${WIWI_DISABLE_REDIS:-0}" = "1" ]; then
    echo "==> Redis disabled (WIWI_DISABLE_REDIS=1); backend uses the in-memory cache"
elif ! command -v docker >/dev/null 2>&1; then
    echo "==> docker not found; backend uses the in-memory response cache" >&2
else
    echo "==> Starting Redis in Docker (host port $REDIS_PORT) ..."
    if ensure_redis; then
        # Exported before the backend launches. The Python server reads
        # REDIS_URL directly; the Go config reads it as an env fallback.
        export REDIS_URL="redis://127.0.0.1:${REDIS_PORT}/0"
        echo "    redis ready; REDIS_URL=$REDIS_URL"
    else
        echo "    WARN: redis not reachable; backend falls back to the in-memory cache" >&2
    fi
fi

# --- 5. launch the chosen backend -------------------------------------------
# Use process substitution (> >(...)) for prefixing so $! captures the real
# process PID, not the pipeline subshell. This makes liveness checks and
# cleanup work on the actual server process.

echo "==> Starting wiwi backend ($BACKEND) on :$PROXY_PORT ..."
if [ "$BACKEND" = "go" ]; then
    # No hot reload: the binary was built above. --config and --port are passed
    # explicitly; running from the repo root lets the SPA static dir resolve.
    "$GO_BIN" --config "$SCRIPT_DIR/wiwi.yaml" --port "$PROXY_PORT" \
        > >(prefixed "backend[go]") 2>&1 &
    BACKEND_PID=$!
else
    CMD_ARGS=(--config "$SCRIPT_DIR/wiwi.yaml" --port "$PROXY_PORT")
    if [ "$RELOAD" = "1" ]; then
        CMD_ARGS+=(--reload)
        IFS=',' read -ra DIRS <<< "$RELOAD_DIRS"
        for d in "${DIRS[@]}"; do
            CMD_ARGS+=(--reload-dir "$d")
        done
    fi
    if [ -n "$WIWI_BIN" ]; then
        # Explicit override only — skips the repo pin above. Make sure it points
        # at the code you intend to run.
        echo "    (using WIWI_BIN override: $WIWI_BIN)"
        "$WIWI_BIN" "${CMD_ARGS[@]}" > >(prefixed "backend") 2>&1 &
    else
        "$PYBIN" -m wiwi.main "${CMD_ARGS[@]}" > >(prefixed "backend") 2>&1 &
    fi
    BACKEND_PID=$!
fi

# --- 6. launch web dev server ------------------------------------------------

echo "==> Starting Vite dev server (web) on :$WEB_PORT ..."
cd "$WEB_DIR"
"${DEV_CMD[@]}" > >(prefixed "web") 2>&1 &
WEB_PID=$!
cd "$SCRIPT_DIR"

echo ""
echo "==> Both running. Backend ($BACKEND): http://localhost:$PROXY_PORT  Web: http://localhost:$WEB_PORT"
echo "==> Press Ctrl-C to stop both."
echo ""

# --- 7. wait — if either dies, stop the other --------------------------------

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
