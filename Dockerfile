# wiwi — multi-stage build
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
RUN uv venv /app/.venv && uv pip install -p /app/.venv/bin/python .

FROM python:3.12-slim-bookworm
RUN useradd -m -u 10001 wiwi
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY wiwi.yaml.example /app/wiwi.yaml.example
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER wiwi
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:4000/health')" || exit 1
ENTRYPOINT ["wiwi"]
CMD ["--config", "/app/wiwi.yaml"]
