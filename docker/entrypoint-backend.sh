#!/bin/sh
# entrypoint-backend.sh — exec the Python backend (the only backend).
# Called from the container's ENTRYPOINT. `set -eu` guarantees a missing binary
# fails the container loudly instead of looping as an unhealthy ghost.
#
# No --port here on purpose: wiwi.main resolves the listen port itself as
# explicit flag > platform-injected PORT > wiwi.yaml, and a platform that
# injects PORT (Railway, Render, Fly) must get the gateway on THAT port. An
# --port passed here would be the "explicit flag" and silently win, leaving
# the container answering where the platform's edge proxy never looks.
# WIWI_CONFIG_PATH overrides the default /app/wiwi.yaml.
set -eu

CFG="${WIWI_CONFIG_PATH:-/app/wiwi.yaml}"

echo "==> wiwi: starting backend (Python)"
exec wiwi --config "$CFG" --host 0.0.0.0
