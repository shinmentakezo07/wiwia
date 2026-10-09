# wiwi — multi-stage build
# Stage 1: Python dependencies + package install
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS py-builder
WORKDIR /app
COPY pyproject.toml ./
COPY wiwi/ /app/wiwi/
# [redis] extra: the response cache falls back to the in-memory backend when
# the package is missing, but then a configured redis_url silently does
# nothing. Install it so REDIS_URL works in the shipped image.
# [otel] extra: same reasoning for telemetry.enabled — without the SDK a
# configured collector is silently a no-op, which looks like a broken export.
RUN uv venv /app/.venv && uv pip install -p /app/.venv/bin/python ".[redis,otel]"

# Stage 2: Build the admin web UI (React + TypeScript → static assets).
# Node 24 matches web/package.json `engines` (>=24) and runs the same
# `npm ci` install path as start.sh and local dev — one package manager.
FROM node:24-bookworm-slim AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 3: Final runtime image
FROM python:3.12-slim-bookworm
RUN useradd -m -u 10001 wiwi
WORKDIR /app

# Python venv from the builder
COPY --from=py-builder /app/.venv /app/.venv

# Built web assets — the vite build outputs to wiwi/server/static/
# Set WIWI_STATIC_DIR so app.py finds them (defaults to the venv's
# site-packages path which won't have the built assets).
COPY --from=web-builder /wiwi/server/static/ /app/wiwi/server/static/
ENV WIWI_STATIC_DIR=/app/wiwi/server/static

# Ship the example config as the default wiwi.yaml so the container boots
# without a volume mount. Set env vars (OPENAI_API_KEY, etc.) to activate
# providers; absent keys are filtered out at load time.
COPY wiwi.yaml.example /app/wiwi.yaml

# Writable data dir for SQLite DB (mounted as a volume in docker-compose).
# /data is also the only writable path on a HuggingFace Docker Space, where
# no volume is mounted — deploy/hf_space.sh points DATABASE_URL there.
#
# /data also holds the stream journal (AUDIT #380). The configured default is
# the RELATIVE `.wiwi/journals`, which resolves against WORKDIR (/app) — and
# /app is owned by root while the process runs as USER wiwi, so nothing could
# create it. `stream_journal_enabled` defaults true, so that made every
# virtual-key STREAMING request fail 503 with "stream replay journal
# unavailable" while master streams and non-streaming calls kept working, and
# the container still reported healthy. Naming the dir explicitly (below, via
# WIWI_STREAM_JOURNAL_DIR) keeps journals on the same path as the SQLite DB,
# so they share one persistence story: persistent where a volume is mounted,
# ephemeral on Railway otherwise.
RUN mkdir -p /app/data && chown wiwi:wiwi /app/data \
    && mkdir -p /data && chown wiwi:wiwi /data \
    && mkdir -p /data/journals && chown wiwi:wiwi /data/journals

ENV WIWI_STREAM_JOURNAL_DIR=/data/journals

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite+aiosqlite:////app/data/wiwi.db

USER wiwi
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:4000/health')" || exit 1
ENTRYPOINT ["wiwi"]
CMD ["--config", "/app/wiwi.yaml"]
