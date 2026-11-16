# wiwi-go shared API contract (single source of truth)

All Go packages live under module `wiwi-go`. Foundation packages (already written, read
their .go files directly): `internal/ir`, `internal/streaming` (deltas.go only),
`internal/providers` (base.go + registry.go), `internal/core` (context.go).

Dependencies allowed: stdlib, `github.com/gin-gonic/gin`, `gopkg.in/yaml.v3`,
`github.com/lib/pq`, `github.com/mattn/go-sqlite3`, `github.com/gorilla/websocket`,
`github.com/google/uuid`. DO NOT run `go get`/`go mod tidy` (deps are pre-added).
DO NOT edit go.mod / go.sum. DO NOT edit files outside the directories assigned to you.
Make sure your package compiles with `go build ./internal/<yourdir>/...`.

---

## internal/config  (package config)

Parse `wiwi.yaml` shape (LiteLLM-like). Provide env interpolation: any string
`os.environ/NAME` resolves from env (missing => ""). A `.env` file in cwd is loaded
first (KEY=VALUE lines, never override an existing env var). Config precedence:
`--config` path > `WIWI_CONFIG` env (inline YAML) > `wiwi.yaml`.

```go
type KeyDef struct { Label, Key string; Weight int; Enabled bool }
type Provider struct {
    Name, ProviderType, BaseURL string
    TimeoutS float64
    ExtraHeaders map[string]string
    RoundRobin bool
    Keys []KeyDef
    AliasID string
}
type DeploymentParams struct {
    Provider, Model string
    Weight int
    MaxTokens, RPM, TPM, MaxInflight *int
    Timeout *float64
    ExtraHeaders map[string]string
    ExtraBody map[string]any
}
type ModelEntry struct { ModelName string; Params DeploymentParams }
type AliasEntry struct { Target string; ForceMapping bool; DisplayName string }
type RouterSettings struct {
    Strategy string
    NumRetries, AllowedFails, KeyMaxConsecutiveFails, CycleEveryN int
    Timeout, CooldownTime float64
    FailoverMode string
    Fallbacks, ContextWindowFallbacks map[string][]string
    ModelGroupAlias map[string]AliasEntry
    GlobalRPM, GlobalTPM *int
    SessionAffinity, PrefixAffinity bool
    StreamIdleTimeoutS, StreamFirstChunkTimeoutS, StreamPingIntervalS float64
    StreamLoopDetection, StreamCoalesce, StreamEventIDs, StreamJournalEnabled bool
    StreamLoopLimit, StreamResumeMaxRetries, StreamJournalMaxBytes int
    StreamResume, StreamJournalDir string
    PrometheusEnabled bool
    PrometheusPath string
}
type GeneralSettings struct {
    MasterKey, DatabaseURL, RedisURL string
    MaxKeysPerUser int
    UnpricedModelPolicy string
    TrustedProxies []string
    SharedDatabase bool
}
type WiwiSettings struct {
    DropParams bool
    MaxRequestBodyMB int
    StoreResponses bool
    ResponseStoreTTLS float64
    Host string
    Port int
    PublicURL string
    SpendBatchingEnabled, SingleflightEnabled, PerfTimings bool
}
type CacheSettings struct { Enabled bool; TTLS float64; MaxEntries int; BypassHeader string }
type HealerSettings struct { Enabled bool; TickS, ProbeTimeoutS float64; MaxProbesPerSweep int }
type Config struct {
    Providers []Provider
    ModelList []ModelEntry
    Router RouterSettings
    General GeneralSettings
    Wiwi WiwiSettings
    Cache CacheSettings
    Healer HealerSettings
}
func Load(path string) (*Config, error)
func LoadFromString(raw string) (*Config, error)
func LoadEnv()
func Resolve(configPath string) (*Config, error)
func (c *Config) ApplyDefaults()   // fill all zero/default fields sensibly
func (c *Config) Validate() error  // provider refs exist, weights>=1
```

YAML keys (use yaml tags): top-level `providers` (each has `name`, `provider`,
`base_url`, `timeout_s`, `extra_headers`, `round_robin`, `keys` [{label,key,weight,enabled}],
`alias_id`), `model_list` (each has `model_name`, `wiwi_params` {provider,model,weight,
max_tokens,rpm,tpm,max_inflight,timeout,extra_headers,extra_body}), `router_settings`,
`general_settings`, `wiwi_settings`, `cache_settings`, `healer`.
Filter out providers whose every key resolved to empty (log via log.Printf), and any
model_list entries referencing removed providers. Fail closed if master_key empty.

---

## internal/store  (package store)

Thin `database/sql` wrapper supporting SQLite and Postgres. Normalize URLs:
`sqlite+aiosqlite:///path` / `sqlite:///path` => sqlite driver (file path after ///).
`postgresql+asyncpg://` => `postgres://`. `postgres://` as-is. Default sqlite: `wiwi.db`.

```go
type Store struct { /* unexported */ }
func Open(databaseURL string) (*Store, error)
func (s *Store) DB() *sql.DB
func (s *Store) Driver() string   // "sqlite" | "postgres"
func (s *Store) Close() error
func (s *Store) Migrate() error   // CREATE TABLE IF NOT EXISTS for all tables below
```
Tables (columns minimal but complete, use TEXT for ids/timestamps as unix ints or
ISO strings, INTEGER/FLOAT for numerics; use a `placeholder(i int)` helper since sqlite
uses `?` and postgres uses `$1`):
- `vkeys(id TEXT PK, name TEXT, key_hash TEXT UNIQUE, max_budget REAL, spend REAL,
   rpm INTEGER, tpm INTEGER, models TEXT, enabled INTEGER, expires_at INTEGER,
   created_at INTEGER, budget_reserved REAL)`
- `users(id TEXT PK, username TEXT UNIQUE, pw_hash TEXT, role TEXT, created_at INTEGER)`
- `sessions(id TEXT PK, user_id TEXT, created_at INTEGER, expires_at INTEGER)`
- `request_logs(id TEXT PK, request_id TEXT, ts INTEGER, key_id TEXT, model_group TEXT,
   provider TEXT, model_id TEXT, status INTEGER, prompt_tokens INTEGER,
   completion_tokens INTEGER, cached_tokens INTEGER, reasoning_tokens INTEGER,
   cost REAL, latency_ms INTEGER, ttft_ms INTEGER, error TEXT, attempts TEXT)`
- `audit_logs(id TEXT PK, ts INTEGER, actor TEXT, action TEXT, target TEXT, diff TEXT)`
- `providers(name TEXT PK, provider_type TEXT, base_url TEXT, config TEXT)`
- `deployments(id TEXT PK, model_name TEXT, provider TEXT, model TEXT, weight INTEGER, config TEXT)`
- `settings(key TEXT PK, value TEXT)`
- `model_prices(model TEXT PK, prompt REAL, completion REAL, cached REAL)`
- `responses(id TEXT PK, key_id TEXT, body TEXT, created_at INTEGER, expires_at INTEGER)`

Provide generic helpers:
```go
func (s *Store) Exec(q string, args ...any) error
func (s *Store) Query(q string, args ...any) (*sql.Rows, error)
func (s *Store) QueryRow(q string, args ...any) *sql.Row
```
Use WAL + foreign_keys for sqlite. Return clean errors. This is the ONLY package that
imports the sql drivers.

---

## internal/cost  (package cost)

```go
type ModelPrice struct { Prompt, Completion, Cached float64 } // USD per token
type Engine struct { /* mutex */ }
func NewEngine() *Engine
func (e *Engine) Upsert(model string, p ModelPrice)
func (e *Engine) Delete(model string)
func (e *Engine) All() map[string]ModelPrice
func (e *Engine) Load(prices map[string]ModelPrice)
func (e *Engine) Compute(model string, u ir.Usage) (cost float64, priced bool)
```
Bundled fallback price table for common models (gpt-4o, gpt-4o-mini, claude-*, etc).
Compute: prompt = (prompt_tokens - cached*... ) etc; keep it simple and deterministic.
`priced=false` when no row/price for model.

---

## internal/ratelimit  (package ratelimit)

```go
type Limiter struct { /* sharded mutex + sliding windows */ }
func New() *Limiter
func (l *Limiter) Allow(bucket string, rpm, tpm, estTokens int) (ok bool, retryAfter float64)
```
Sliding 60s window. rpm<=0 / tpm<=0 => unlimited. Bucket string is the key id + kind.

---

## internal/cache  (package cache)

```go
type Cache struct { /* map + ttl + LRU-ish cap */ }
func New(enabled bool, ttlS float64, maxEntries int) *Cache
func (c *Cache) Get(key string) ([]byte, bool)
func (c *Cache) Set(key string, val []byte)
func (c *Cache) Enabled() bool
type Flight struct { /* singleflight */ }
func NewFlight() *Flight
func (f *Flight) Do(key string, fn func() ([]byte, error)) ([]byte, error)
```

---

## internal/logging  (package logging)

```go
type Event struct { ID int64; Ts int64; Type string; Data map[string]any }
type Bus struct { /* ring buffer + subscribers */ }
func NewBus(cap int) *Bus
func (b *Bus) Publish(typ string, data map[string]any) int64
func (b *Bus) EventsSince(id int64) []Event
func (b *Bus) Subscribe(since int64) (<-chan Event, func()) // returns channel + cancel
type Totals struct { /* atomic-ish counters, mutex */ }
func NewTotals() *Totals
func (t *Totals) AddRequest(prompt, completion int, cost float64, isErr bool)
func (t *Totals) Snapshot() map[string]float64
```
`Publish` assigns monotonic ID starting at 1. Thread-safe.

---

## internal/metrics  (package metrics)

```go
type Collector struct { /* counters map + mutex */ }
func NewCollector() *Collector
func (c *Collector) Inc(name string, v float64)
func (c *Collector) Set(name string, v float64)
func (c *Collector) Render() string  // Prometheus text exposition format
```

---

## internal/wire  (package wire)

Codecs translate inbound dialect JSON <-> ir.Request/ir.AssistantTurn and encode
streaming deltas (streaming.Delta) into SSE `data:` frame payloads. Four dialects:
chat, responses, messages, completions(legacy). Also per-dialect error bodies.

```go
type Surface string
const ( SurfaceChat Surface="chat"; SurfaceResponses Surface="responses"
        SurfaceMessages Surface="messages"; SurfaceCompletions Surface="completions" )

// Request decode: raw JSON body (map) -> ir.Request. Return error -> 400.
func DecodeRequest(s Surface, body map[string]any) (ir.Request, error)

// Non-streaming response encode: IR turn -> full JSON body (map).
func EncodeResponse(s Surface, turn ir.AssistantTurn, model string, req ir.Request) map[string]any

// Streaming encoder: stateful object per stream.
type StreamEncoder interface {
    Start() []string                 // SSE data: payload strings (may be empty)
    Encode(d streaming.Delta) []string
    End() []string
}
func NewStreamEncoder(s Surface, model, reqID string) StreamEncoder

// Error bodies in each dialect's shape.
func ErrorBody(s Surface, status int, eType, message string) (int, map[string]any)
```
Notes: chat uses `choices[].delta`; messages uses Anthropic `content_block_*` events;
responses uses `response.*` events; completions uses `choices[].text`. All streaming
encoders must buffer UsageFinal last-write-wins, emit [DONE]/terminal at End. Follow
the streaming delta contract in internal/streaming/deltas.go. Handle tool calls
(ToolCallOpen/ArgsDelta/Close), thinking, ServerToolResultDelta, LogprobsDelta.

---

## internal/providers  (package providers) — adapters only

DO NOT edit base.go / registry.go (owned by integrator). Read them: they define
`Adapter`, `WiwiError`, `ProviderKeyRef`, `Register`, `GetAdapter`, `DoRequest`,
helper funcs (AsDict/AsList/AsStr/CoerceArgsFragment/ErrorFromProviderStatus/
StatusForKeyPool). The `Adapter` interface:
```go
type Adapter interface {
    ProviderType() string
    Headers(key ProviderKeyRef) map[string]string
    BuildURL(baseURL, modelID string, stream bool) string
    EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error)
    DecodeResponse(status int, body []byte) (ir.AssistantTurn, error)
    DecodeStreamEvent(event, data string) ([]streaming.Delta, error)
    Reset()
}
```
Implement ALL 13 provider types from config.PROVIDER_TYPES:
`openai, anthropic, gemini, openai-compatible, openrouter, gmicloud, bai,
nvidia-nim, cline, workbuddy, opencode, hermes, ifm`.
- 7 share the OpenAI Chat wire: openai, openai-compatible, gmicloud, bai, openrouter,
  nvidia-nim, opencode -> one OpenAIAdapter (register all these names to it).
- anthropic -> AnthropicAdapter (Messages API, x-api-key header, content blocks, SSE events).
- gemini -> GeminiAdapter (generateContent / streamGenerateContent).
- cline, workbuddy, hermes, ifm -> thin OpenAI-wire variants (register OpenAIAdapter
  or a small variant; a comment noting OAuth is out of scope is fine) so all 13 resolve.
Each adapter must implement EncodeRequest (IR->provider JSON), DecodeResponse
(provider JSON->IR AssistantTurn incl. usage/tool_calls/stop_reason), and
DecodeStreamEvent (SSE data -> []streaming.Delta). Handle `[DONE]`.
Register every adapter name in an init() so GetAdapter never fails for a known type.
Keep per-stream decode state on the adapter struct (open tool indices etc.) and clear
it in Reset().

---

## internal/server  (integrator-owned; others DO NOT touch)

Holds AppState (config, store, cost, ratelimit, cache, logging.Bus, metrics.Collector,
auth service, router, gateway, response store, stream journal, SPA). Implements ALL
routes with Gin: /health, /v1/*, /admin/*, /auth/*, /public/*, /metrics, static SPA.
