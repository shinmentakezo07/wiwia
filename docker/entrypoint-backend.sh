#!/bin/sh
# entrypoint-backend.sh — exec the backend selected by BACKEND (go | py).
# Called from the container's ENTRYPOINT; CMD carries the default positional
# args for the Python backend and is ignored by the Go branch (its flags are
# mirrored below). `set -eu` guarantees a missing binary fails the container
# loudly instead of looping as an unhealthy ghost.
set -eu

BACKEND="${BACKEND:-py}"
PORT="${PORT:-4000}"
CFG="${WIWI_CONFIG_PATH:-/app/wiwi.yaml}"

case "$BACKEND" in
    go|golang|GO|Go)
        echo "==> wiwi: starting Go backend (wiwi-go)"
        # --config and --port are the Go CLI's only flags (--host does not
        # exist there); wiwi-go binds 0.0.0.0 by default, which is what the
        # container needs. --port wins over any config value.
        exec /app/wiwi-go/bin/wiwi --config "$CFG" --port "$PORT"
        ;;
    py|python|python3|PY|Py|"")
        echo "==> wiwi: starting Python backend (wiwi)"
        exec wiwi --config "$CFG" --host 0.0.0.0 --port "$PORT" "$@"
        ;;
    *)
        echo "ERROR: unknown BACKEND '$BACKEND' (use: go | py)" >&2
        exit 1
        ;;
esac
