# wiwi — Provider Setup Guide

wiwi ships **12 provider types** (`PROVIDER_TYPES` in `wiwi/config.py`): `openai`, `anthropic`, `gemini`, `openai-compatible`, `openrouter`, `gmicloud`, `bai`, `nvidia-nim`, `cline`, `workbuddy`, `opencode`, `hermes`. Each maps to an adapter in `wiwi/providers/` that encodes IR → provider wire format and decodes responses/stream events back to IR.

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
| `hermes` | Chat Completions | Nous API key **or** OAuth device-code token (`hermes:` prefix) | Nous Portal (`inference-api.nousresearch.com`); no Realtime WS surface |

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

Upstreams here vary more than the cloud APIs do, and the adapter absorbs the differences. One worth knowing about when reading a stream on the wire: OpenAI sends a tool call's `id` and `name` only on its **first** chunk, while several of these servers repeat both on *every* arguments chunk. That is read as one continuing tool call, not one call per chunk, so a call streamed over N fragments stays a single `tool_use` block with the arguments accumulated into it. A genuinely new call on the same index — a different `id` — still closes the previous one and opens its own block.

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
- Declares `force_stream` (the same `transport.forceStream: true` OpenCode's own provider entry carries): Zen answers as an event stream, so every upstream request asks for SSE and a non-streaming caller's reply is reassembled by the gateway's pump — like `cline` and `workbuddy`. The `gemini` route selects its wire from the URL (`:streamGenerateContent?alt=sse`), so its body carries no `stream` field. Also forces `stream_options.include_usage` on chat requests so an aggregated turn prices on real usage rather than the estimator.
- Carries the official client's metadata headers (`x-opencode-session` + `x-opencode-session-id`, `x-opencode-client`, `x-opencode-project`), with `x-opencode-session` **reused per credential** rather than minted per request, and refreshes its `opencode/<version>` User-Agent live (`opencode_version.py`) so the upstream sees a current client version (below ~1.19 is `426 UpgradeRequired`; the cold-cache fallback is a real version, because `opencode/unknown` is a `403`).

**The free tier is a request-shape gate, and the shape is the client's.** `*-free` models (and the stealth `big-pickle`) answer `200` only for a request presenting the current client's fingerprint. The adapter supplies it, derived by capturing the real client and bisecting its request (2026-10-14): the tool payload must contain `read` and `shell` **carrying the client's real parameter schemas** (v1's pair was `bash`/`read`; v2 renamed `bash`), `Authorization` must be present (`Bearer public` when anonymous), `x-opencode-session-id` must ride alongside `x-opencode-session`, and `HTTP-Referer`/`x-opencode-request` must be absent — the client sends neither and each one alone turns a 200 into a 403. The `User-Agent` must carry a version the upstream still accepts (`opencode/unknown` 403s, below ~1.19 is `426`); the version helper's cold-cache fallback is therefore a real version, not a placeholder. A policy refusal still surfaces as `402 … requires billing (403)` and leaves the key pool alone, so the request fails over instead of retiring healthy keys.

The decoys are inert — the description is replaced so the model is told not to call them — and never replace a client tool of the same name; any call that comes back anyway is dropped on both the streaming and the aggregated path, with the finish reason corrected when that call was the only one. All of it is behind `opencode_free_tier_cloak`, which is **on by default** (the gate needs it) and set to `false` for a deployment that serves only paid models.

> The full method, the measured factor matrix, the per-wire routing table, triage steps for a 403, and the procedure for re-deriving the shape when the client moves again are in **[OPENCODE.md](OPENCODE.md)**. Read it before changing anything in this section — the gate has moved once already (v1 → v2 renamed `bash` to `shell`).

```yaml
providers:
  - name: opencode-main
    provider: opencode
    base_url: https://opencode.ai/inference/v1   # default; legacy /zen/v1 403s the free tier
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

## `hermes`

Hermes (Nous Portal, `inference-api.nousresearch.com`) speaks OpenAI Chat
Completions, so `HermesAdapter` is a thin subclass of `OpenAIAdapter`. The only
override is `realtime_url() -> None`: Portal exposes no Realtime WebSocket
surface, so the adapter declines it rather than inheriting an OpenAI URL that
would 404 mid-session (see § Realtime below). Everything else — tools,
streaming, `reasoning_effort`, image inputs, prompt-cache usage — is the
reference OpenAI behaviour.

```yaml
providers:
  - name: hermes-main
    provider: hermes
    base_url: https://inference-api.nousresearch.com/v1   # default
    keys:
      - {label: main, key: os.environ/HERMES_API_KEY}
```

Authenticate with a Nous API key **or** connect through OAuth (device-code) so
you never handle a key at all. Latest models: `Hermes-4-405B`, `Hermes-4-70B`,
`Hermes-4.3-36B`.

### Two ways to authenticate

| Method | When to use | Where the credential lives |
|---|---|---|
| **API key** | Simple, scripted setups | A `keys[].key` entry (or the admin key pool) — sent as `Authorization: Bearer <key>` |
| **OAuth (device-code)** | Sign in with a Nous Portal account; no key to paste or rotate | The account's own pool key, stored as a `hermes:<json>` credential blob (access + refresh + expiry); the adapter resolves the bearer from it |

### Multiple accounts, one pool

Each Nous Portal account is a **separate OAuth identity** with its own rotating
(single-use) refresh token and its own rate-limit bucket (~50 RPM / 50k TPM).
wiwi stores **one account per provider pool key** as a self-contained
`hermes:<json>` blob, so the normal key pool and its smooth weighted
round-robin spread traffic across every connected account and beat any single
account's cap. Connecting a second account **appends** a new pool key (labelled
by the account's email, de-duplicated `…-2`, `…-3`); it never overwrites an
existing one. Each account's tokens are refreshed on its own — a rotation on one
account never touches a sibling, and one account whose refresh grant dies
(`invalid_grant`) is removed from rotation without affecting the rest.

A bare `hermes:<token>` (paste-a-token, no JSON) still works for inference but
carries no refresh grant, so it is never auto-refreshed.

### OAuth device-code flow (RFC 8628)

Nous Portal implements OAuth 2.0 device authorization. There is **no callback
URL** and **no client secret** — the flow is:

1. **Start** — `POST /admin/hermes/oauth/device-code?provider=<name>`. wiwi asks
   Portal for a device code and returns a verification URL plus a short user
   code. If the account does not exist yet it is **created automatically** with a
   placeholder pool key, so no API key is ever required up front.
2. **Approve** — open `verification_uri_complete` in a browser and sign in to
   Nous Portal. The user code is pre-filled by the link.
3. **Poll** — `POST /admin/hermes/oauth/poll` with the returned `state`. wiwi
   forwards one poll to Portal; while you have not approved it answers
   `{"pending": true}` (the UI retries every ~5 s). Once approved it **appends
   the account as a new pool key** holding a `hermes:<json>` credential blob and
   returns `{"connected": true, "key_label", "access_token_masked", "expires_at"}`.

Do all of this from the admin UI at **`/console/hermes-oauth`** — the Connect
button drives the three steps above and shows a live "waiting for approval"
state until the browser sign-in completes.

- **Refresh:** a background sweeper rotates each account ~2 minutes before its
  access token expires (a ~60 s tick), and the gateway also rotates on a
  mid-session `401` and retries once. You can force a rotation from
  `POST /admin/hermes/oauth/refresh` (optionally `key_label` for one account). A
  terminal grant (`invalid_grant`, or a reused/revoked refresh token) returns
  `401`, stops that account's refresh retries, and requires a fresh device-code
  login for it — the other accounts keep serving.
- **Status / disconnect:** `GET /admin/hermes/oauth/status` lists every account
  (`accounts[]`, each with its own expiry / `needs_refresh` / `has_refresh_token`).
  `DELETE /admin/hermes/oauth/disconnect` removes one account (`key_label`) or,
  with no label, the whole pool.
- **Bearer:** the stored access token is sent upstream as
  `Authorization: Bearer <token>`; `HermesAdapter.headers()` resolves it from the
  blob (the `hermes:` prefix and JSON never reach the wire). Nous accepts the
  access token alone — the CLI's `agent_key` is the same JWT — so no second
  credential is needed.

The endpoint contract is implemented in `wiwi/providers/hermes_oauth.py` (plus
`hermes_auto_refresh.py` for the per-account rotation), verified against the
Hermes Agent CLI's own OAuth client (`hermes_cli/auth_nous.py` +
`auth_device_flow.py`) and the live Portal endpoints
(`portal.nousresearch.com/api/oauth/device/code`, `…/api/oauth/token`). See
[API_REFERENCE.md](API_REFERENCE.md) § OAuth providers (Hermes) for the full
request/response shapes.


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

## Realtime

Only some providers expose a Realtime-style WebSocket. An adapter declares it
with `realtime_url(base_url)`, derived from the deployment's configured base URL:

```python
>>> adapter.realtime_url("https://api.openai.com/v1")
'wss://api.openai.com/v1/realtime'
```

The capability is per adapter, not per wire shape. The four OpenAI-shaped
providers (`openai`, `openai-compatible`, `gmicloud`, `bai`) implement it;
`openrouter`, `nvidia-nim`, `cline` and `workbuddy` share the chat wire shape
but **not** the realtime protocol, and answer `501` rather than inheriting a URL
that would 404 mid-session. `anthropic`, `gemini`, `opencode` and `hermes` have
no realtime surface at all.

Relays need `realtime.enabled` in the config (off by default). A session is
admitted through the ordinary path — the same auth, budgets, cooldowns, key
rotation and `max_inflight` caps as an HTTP request — and then its frames pass
through untouched. wiwi does not translate the session protocol: it is stateful
and ordered, and reimplementing it would mean reimplementing the upstream's
session store.

Billing is on the session's **peak** usage, charged once at close: the peak
(not the sum of its usage-bearing events, which restate overlapping windows) is
priced and applied to the virtual key's spend, so a realtime session counts
against its `max_budget` exactly like a completion. A session that fails to be
charged (a transient write failure) is logged and counted in
`spend_charge_failures` rather than silently dropped, so an unenforced cap stays
visible in `/health` and `/metrics`.

## Provider quirks live in adapters — nowhere else

The binding invariant: all dialect/provider branching stays inside `wiwi/wire/` and `wiwi/providers/`. `core/`, `router/`, `auth/`, `streaming/` must never import dialect or provider symbols. If you find yourself special-casing a provider in the gateway or router, it belongs in the adapter.
