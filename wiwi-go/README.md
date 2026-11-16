# wiwi-go

A Go (Gin) port of the [wiwi](../wiwi) unified LLM gateway: OpenAI Chat /
Responses and Anthropic Messages in, any of 13 provider types out, translated
through one canonical IR.

## Build & run

```bash
cd wiwi-go
CGO_ENABLED=1 go build -o wiwi ./cmd/wiwi
./wiwi --config wiwi.yaml          # default :4000
```

`CGO_ENABLED=1` is required for the SQLite driver. Postgres works with
`DATABASE_URL=postgres://…` (or `postgresql+asyncpg://…`).

## Config

Same `wiwi.yaml` shape as the Python gateway (providers / model_list /
router_settings / general_settings / wiwi_settings / cache_settings). See
`wiwi.yaml.example`. Any string may be `os.environ/NAME`; a `.env` in the cwd is
loaded first.

## Layout

```
cmd/wiwi                 CLI entrypoint
internal/config          YAML config + env interpolation + validation
internal/ir              canonical IR
internal/wire            inbound codecs (4 dialects) + stream encoders + error bodies
internal/providers       outbound adapters (13 types) + registry + error classification
internal/router          model groups, WRR, cooldowns, fallbacks, aliases
internal/core            RequestContext + gateway (complete/stream, retries, pricing)
internal/streaming       delta taxonomy, SSE, coalescer, loop detector, tape, journal
internal/auth            virtual keys, budgets, users, sessions
internal/ratelimit       sliding-window limiter
internal/cache           response cache + singleflight
internal/cost            pricing engine
internal/logging         event bus + totals
internal/metrics         Prometheus text collector
internal/store           SQLite/Postgres persistence + schema
internal/server          Gin app, all /v1, /admin, /auth routes, SPA mount
```

## Test

```bash
CGO_ENABLED=1 go test ./...
go vet ./...
```

See `PARITY.md` for coverage and `MIGRATION.md` for the phase log.
