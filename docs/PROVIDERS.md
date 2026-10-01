# wiwi — Provider Setup Guide

wiwi ships **11 provider types** (`PROVIDER_TYPES` in `wiwi/config.py`): `openai`, `anthropic`, `gemini`, `openai-compatible`, `openrouter`, `gmicloud`, `bai`, `nvidia-nim`, `cline`, `workbuddy`, `opencode`. Each maps to an adapter in `wiwi/providers/` that encodes IR → provider wire format and decodes responses/stream events back to IR.

Adding a provider = new adapter module + one branch in `get_adapter()` (`wiwi/providers/registry.py`) + the entry in `PROVIDER_TYPES`. The registry has an import-time assert that fails loudly if a type has no branch.

## Quick matrix

| Type | Wire format to provider | Auth | Notes |
|---|---|---|---|
| `openai` | Chat Completions | Bearer key | Reference adapter |
| `openai-compatible` | Chat Completions | Bearer/none | Ollama, vLLM, LM Studio, … (needs `base_url`) |
| `anthropic` | Messages API | `x-api-key` | SSE event folding |
| `gemini` | `generateContent` REST | `?key=` query param | `alt=sse` streaming |
| `openrouter` | Chat Completions | Bearer key | `reasoning` param translation, `reasoning_details` |
| `gmicloud` | Chat Completions | Bearer key | OpenAI-format endpoint |
| `bai` | Chat Completions | Bearer key | B.AI unified gateway; one key, three protocols — wiwi speaks Chat to it |
| `nvidia-nim` | Chat Completions | Bearer key | vLLM-backed quirks: reasoning via `chat_template_kwargs`, tool-schema rewrite |
| `cline` | Chat Completions | WorkOS OAuth bearer (`workos:` prefix) | OAuth + live Cline CLI/core fingerprint + auto-refresh |
| `workbuddy` | Chat Completions | Access token from auth JSON | Tencent CodeBuddy quirks + auto-refresh |
| `opencode` | Per-model: 4 upstream protocols | Bearer key | Multi-protocol routing, live User-Agent refresh |

---

## `openai`

The reference adapter. Everything OpenAI-wire-shaped flows through `OpenAIAdapter`.

```yaml
providers:
  - name: openai-main
    provider: openai
    base_url: https://api.openai.com/v1   # default
    keys:
      - {label: main, key: os.environ/OPENAI_API_KEY}
```

Supports: streaming, tools, parallel tool calls, `reasoning_effort`, structured outputs (`response_format`), prompt-cache usage reporting, image inputs.

## `openai-compatible`

Any endpoint speaking OpenAI Chat Completions. `base_url` is required.

```yaml
providers:
  - name: ollama
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    keys:
      - {label: local, key: "not-needed"}     # often unneeded
  - name: vllm
    provider: openai-compatible
    base_url: http://vllm.internal:8000/v1
    keys:
      - {label: main, key: os.environ/VLLM_KEY}
```

Model groups can mix cloud + local deployments under one `model_name`; the router failovers across them.

## `anthropic`

Native Messages API (not the OpenAI-compat shim), so Anthropic-only features survive translation in both directions.

```yaml
providers:
  - name: anthropic-main
    provider: anthropic
    base_url: https://api.anthropic.com/v1   # default
    keys:
      - {label: main, key: os.environ/ANTHROPIC_API_KEY}
```

Supports: SSE event folding into the IR delta taxonomy (`content_block_start/delta/stop` → `TextDelta`/`ThinkingDelta`/`ToolCall*`), `thinking` blocks with signatures, prompt-cache tokens (`cache_read_input_tokens`, `cache_creation_input_tokens`), server tools (`web_search` → IR builtin tools).

## `gemini`

Speaks Google's native `generateContent` REST protocol with `alt=sse` streaming — not the OpenAI-compat layer — preserving Gemini-specific mapping.

```yaml
providers:
  - name: gemini-main
    provider: gemini
    base_url: https://generativelanguage.googleapis.com/v1beta   # default
    keys:
      - {label: main, key: os.environ/GEMINI_API_KEY}
```

## `openrouter`

OpenAI-compatible at the wire level with parameter translation:

- Inbound `reasoning_effort` → OpenRouter `reasoning` object (accepts `effort` OpenAI-style or `max_tokens` Anthropic-style).
- Response-side `reasoning_details` arrays folded into IR thinking parts.

```yaml
providers:
  - name: openrouter-main
    provider: openrouter
    base_url: https://openrouter.ai/api/v1   # default
    keys:
      - {label: main, key: os.environ/OPENROUTER_API_KEY}
```

## `gmicloud`

GMICloud's OpenAI-format endpoint. Plain specialization of the OpenAI adapter.

```yaml
providers:
  - name: gmi-main
    provider: gmicloud
    base_url: https://api.gmi-serving.com/v1   # default
    keys:
      - {label: main, key: os.environ/GMI_API_KEY}
```

## `bai`

B.AI unified LLM gateway (`api.b.ai`). Exposes one API key across three protocols (Chat Completions, Responses, Messages); wiwi always speaks Chat Completions to it. Specialization of `OpenAIAdapter`.

```yaml
providers:
  - name: bai-main
    provider: bai
    base_url: https://api.b.ai/v1            # default; see adapter for exact endpoint
    keys:
      - {label: main, key: os.environ/BAI_API_KEY}
```

## `nvidia-nim`

NVIDIA NIM (`integrate.api.nvidia.com`) — OpenAI wire format with three quirks that need a dedicated adapter:

1. **Reasoning via `chat_template_kwargs`** — NIM is vLLM-backed; thinking budget is passed as `chat_template_kwargs` rather than a `reasoning` field.
2. **Tool-schema rewrite** — NIM rejects JSON Schema boolean subschemas (`true`/`false` as schemas) and object properties named `type`. `nim_tool_schema.py` rewrites IR tool schemas into a NIM-safe form before sending.
3. Native tool handling via `nim_native_tools.py`.

```yaml
providers:
  - name: nim-main
    provider: nvidia-nim
    base_url: https://integrate.api.nvidia.com/v1   # default
    keys:
      - {label: main, key: os.environ/NVIDIA_NIM_API_KEY}
```

## `cline`

Cline (`api.cline.bot`) — OpenAI Chat Completions-compatible with three quirks:

1. **Auth**: WorkOS OAuth tokens sent as `Authorization: Bearer workos:<token>` — the `workos:` prefix is mandatory (auto-prepended when missing).
2. **Fingerprint**: every request carries a client-identification header. A background worker
   refreshes the live Cline CLI version from npm (`cline`) and the separate core version from npm
   (`@cline/core`) every five minutes. The CLI version fills `User-Agent`, `X-CLIENT-VERSION`, and
   `X-PLATFORM-VERSION`; `X-CORE-VERSION` uses the core version. Header construction reads only the
   cache, so registry failures never block a request.
3. **OAuth lifecycle**: tokens refresh on demand; auto-refresh runs as a background service.

There is **no** `client_id` / `client_secret` in config — `cline_oauth.py` states "no client_id / no PKCE", and the OAuth flow needs no pre-registered app credentials. Register the account instead, which stores the resulting token in the key pool:

```yaml
providers:
  - name: cline-main
    provider: cline
    base_url: https://api.cline.bot/api/v1   # default
    keys: []                                  # filled in by the OAuth flow
```

Connect via the admin UI (Providers → Cline → Connect) or the OAuth endpoints (`/admin/cline/oauth/*`). The callback lands on `/cline/oauth/callback`.

## `workbuddy`

WorkBuddy / CodeBuddy (Tencent) — OpenAI Chat Completions-compatible with four quirks (ported from workbuddy2api):

1. **Auth**: the key secret is a WorkBuddy **auth JSON** (nested or flat — `workbuddy_auth.py` normalizes both); requests carry the derived access token.
2. Fingerprint/headers as upstream expects.
3. Auto-refresh background service (shared `CircuitBreaker` primitive from `wiwi/core/recovery.py`).
4. Account import/export via admin endpoints.

As with Cline there is **no** `client_id` / `client_secret` — `workbuddy_auth.py` reads no environment variables. Accounts are imported, and the auth JSON lands in the key pool:

```yaml
providers:
  - name: workbuddy-main
    provider: workbuddy
    base_url: https://copilot.tencent.com   # default
    keys: []                                 # filled in by import
```

Import accounts via `/admin/workbuddy/import` or the admin UI.

## `opencode`

OpenCode Zen (`opencode.ai/zen`) — a multi-protocol gateway: the same base URL serves **four upstream wire formats, chosen per model** (see the Zen endpoints table). The adapter:

- Routes each model to its correct upstream protocol.
- Declares `force_stream` (the same `transport.forceStream: true` OpenCode's own provider entry carries): Zen answers as an event stream, so every upstream request asks for SSE and a non-streaming caller's reply is reassembled by the gateway's pump — like `cline` and `workbuddy`. The `gemini` route selects its wire from the URL (`:streamGenerateContent?alt=sse`), so its body carries no `stream` field.
- Satisfies the **free-tier admission gate**. A `*-free` (and the stealth `big-pickle`) request returns `403 FreeTierError` unless it streams, carries a CLI-shaped `x-opencode-session` (`ses_` + 12 hex + 14 base62, **reused per credential** — free quota is accounted per session), and includes the CLI's `bash`/`read` decoy tools; the adapter supplies all three and forces `stream_options.include_usage` so an aggregated chat turn prices on real usage. Only on free models, and a real client tool of the same name is never replaced.
- Keeps those decoys **invisible to clients**: a model may still *call* `bash`/`read` (probed live), and the caller has no such tool, so the adapter drops any tool call aimed at a name it injected itself — on both the streaming and the aggregated path — and corrects the finish reason when that call was the only one.
- The gate is **not a credential check**: free models answer `200` keyless (the `anonymous` sentinel omits `Authorization`) and `429 FreeUsageLimitError` once a real account's free quota is spent. Paid models still need a real `OPENCODE_API_KEY`.
- Refreshes its `opencode/<version>` User-Agent live (`opencode_version.py`) so the upstream sees a current client version (pre-1.17 is `426 UpgradeRequired`).

```yaml
providers:
  - name: opencode-main
    provider: opencode
    base_url: https://opencode.ai/zen/v1     # default
    keys:
      - {label: main, key: os.environ/OPENCODE_API_KEY}
```

---

## Key pools & weights

Config declares a **key pool** per provider account (one or more entries under `keys:`). The admin API manages the same pool at runtime, layering on top of whatever the config file declared. Each key carries:

| Field | Meaning |
|---|---|
| `label` | Identifier for the key (used in URLs, logs, cooldowns) |
| `secret` | The credential (encrypted at rest; reveal endpoint is audit-logged) |
| `weight` | WRR bias when picking among pool keys |
| `enabled` | On/off switch |
| health state | `active` · `cooling` (transient error → cooldown) · `invalid` (auth rejected) · `disabled` · `probation` (healer-restored, reduced WRR weight until it graduates) |

Router picks a deployment, then a key from that deployment's pool. Errors trigger cooldowns; cooldowns expire automatically once `cooldown_time` elapses. The optional HealthHealer probes sick keys with 1-token requests and restores them into `probation` (see [ARCHITECTURE.md](ARCHITECTURE.md) §Recovery).

## Mixing providers in one model group

```yaml
model_list:
  - model_name: claude-sonnet
    wiwi_params:
      provider: anthropic-main          # a providers[].name
      model: claude-sonnet-4-5          # provider-native model id
      weight: 2
  - model_name: claude-sonnet          # same group, second deployment
    wiwi_params:
      provider: openrouter-main         # a different account, not a different syntax
      model: anthropic/claude-sonnet-4.5
      weight: 1
```

Clients always request `claude-sonnet`. WRR splits traffic 2:1; upstream failures failover to the other deployment (and to `router_settings.fallbacks`, if configured) mid-stream with tape-based resume.

## Capacity: concurrency caps and priority lanes

An upstream with a connection limit will happily be handed one socket per
inbound request. `router_settings.max_inflight` bounds that; a request arriving at
a full deployment is refused with `503` and a `Retry-After` rather than queued
(see [CONFIG.md](CONFIG.md) § Load shedding for the full table).

**Cap an expensive model harder than its siblings.** The router-wide default
applies to every deployment unless the model overrides it:

```yaml
router_settings:
  max_inflight: 8

model_list:
  - model_name: gpt-4o
    wiwi_params:
      provider: openai-main
      model: gpt-4o
      max_inflight: 2      # tighter than the default of 8
```

**Keep interactive traffic off a batch job's back.** Lanes partition a
deployment's concurrency by share. The share is capacity a lane may *use* — bulk
cannot consume the slots interactive needs, and interactive is still admitted onto
a deployment bulk already occupies:

```yaml
router_settings:
  max_inflight: 10
  priority_lanes:
    interactive: 0.8       # may hold up to 8 concurrent requests
    bulk: 0.2              # refused at 2
  default_lane: bulk
```

A virtual key declares its lane with the `priority` field (admin API, or the
`vkeys.priority` column). A key with no lane, or one naming a lane the operator
never configured, lands on `default_lane` — never on full capacity. Master-key
requests always get the full cap.

Shares must each be in `(0, 1]` and sum to at most `1.0`; a config that
over-subscribes is rejected at load rather than silently admitting every lane at
full capacity.

## Provider quirks live in adapters — nowhere else

The binding invariant: all dialect/provider branching stays inside `wiwi/wire/` and `wiwi/providers/`. `core/`, `router/`, `auth/`, `streaming/` must never import dialect or provider symbols. If you find yourself special-casing a provider in the gateway or router, it belongs in the adapter.
