# wiwi — multi-stage build
# Stage 1: Python dependencies + package install
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS py-builder
WORKDIR /app
COPY pyproject.toml ./
COPY wiwi/ /app/wiwi/
RUN uv venv /app/.venv && uv pip install -p /app/.venv/bin/python .

# Stage 2: Build the admin web UI (React → static assets)
FROM oven/bun:1 AS web-builder
WORKDIR /web
COPY web/package.json web/bun.lock* ./
RUN bun install --frozen-lockfile || bun install
COPY web/ ./
RUN bun run build

# Stage 3: Final runtime image
FROM python:3.12-slim-bookworm
RUN useradd -m -u 10001 wiwi
WORKDIR /app
COPY --from=py-builder /app/.venv /app/.venv
# Built web assets land alongside the package so app.py finds them
COPY --from=web-builder /wiwi/server/static/ /app/wiwi/server/static/
# Ship the example config as the default wiwi.yaml so the container boots
# without a volume mount.  Set env vars (OPENAI_API_KEY, etc.) to activate
# providers; absent keys are filtered out at load time.
COPY wiwi.yaml.example /app/wiwi.yaml
# Writable data dir for SQLite DB (mounted as a volume in docker-compose)
RUN mkdir -p /app/data && chown wiwi:wiwi /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER wiwi
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:4000/health')" || exit 1
ENTRYPOINT ["wiwi"]
CMD ["--config", "/app/wiwi.yaml"]
