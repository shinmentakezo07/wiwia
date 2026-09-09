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
| `cline` | Chat Completions | WorkOS OAuth bearer (`workos:` prefix) | OAuth + fingerprint header + auto-refresh |
| `workbuddy` | Chat Completions | Access token from auth JSON | Tencent CodeBuddy quirks + auto-refresh |
| `opencode` | Per-model: 4 upstream protocols | Bearer key | Multi-protocol routing, live User-Agent refresh |

---

## `openai`

The reference adapter. Everything OpenAI-wire-shaped flows through `OpenAIAdapter`.

```yaml
providers:
  openai:
    api_key: os.environ/OPENAI_API_KEY
    base_url: https://api.openai.com/v1   # default
```

Supports: streaming, tools, parallel tool calls, `reasoning_effort`, structured outputs (`response_format`), prompt-cache usage reporting, image inputs.

## `openai-compatible`

Any endpoint speaking OpenAI Chat Completions. `base_url` is required.

```yaml
providers:
  ollama:
    type: openai-compatible
    api_key: ""                              # often unneeded
    base_url: http://localhost:11434/v1
  vllm:
    type: openai-compatible
    api_key: os.environ/VLLM_KEY
    base_url: http://vllm.internal:8000/v1
```

Model groups can mix cloud + local deployments under one `model_name`; the router failovers across them.

## `anthropic`

Native Messages API (not the OpenAI-compat shim), so Anthropic-only features survive translation in both directions.

```yaml
providers:
  anthropic:
    api_key: os.environ/ANTHROPIC_API_KEY
    base_url: https://api.anthropic.com      # default
```

Supports: SSE event folding into the IR delta taxonomy (`content_block_start/delta/stop` → `TextDelta`/`ThinkingDelta`/`ToolCall*`), `thinking` blocks with signatures, prompt-cache tokens (`cache_read_input_tokens`, `cache_creation_input_tokens`), server tools (`web_search` → IR builtin tools).

## `gemini`

Speaks Google's native `generateContent` REST protocol with `alt=sse` streaming — not the OpenAI-compat layer — preserving Gemini-specific mapping.

```yaml
providers:
  gemini:
    api_key: os.environ/GEMINI_API_KEY
    base_url: https://generativelanguage.googleapis.com/v1beta   # default
```

## `openrouter`

OpenAI-compatible at the wire level with parameter translation:

- Inbound `reasoning_effort` → OpenRouter `reasoning` object (accepts `effort` OpenAI-style or `max_tokens` Anthropic-style).
- Response-side `reasoning_details` arrays folded into IR thinking parts.

```yaml
providers:
  openrouter:
    api_key: os.environ/OPENROUTER_API_KEY
    base_url: https://openrouter.ai/api/v1   # default
```

## `gmicloud`

GMICloud's OpenAI-format endpoint. Plain specialization of the OpenAI adapter.

```yaml
providers:
  gmicloud:
    api_key: os.environ/GMI_API_KEY
    base_url: https://api.gmicloud.ai/v1     # default
```

## `bai`

B.AI unified LLM gateway (`api.b.ai`). Exposes one API key across three protocols (Chat Completions, Responses, Messages); wiwi always speaks Chat Completions to it. Specialization of `OpenAIAdapter`.

```yaml
providers:
  bai:
    api_key: os.environ/BAI_API_KEY
    base_url: https://api.b.ai/v1            # default; see adapter for exact endpoint
```

## `nvidia-nim`

NVIDIA NIM (`integrate.api.nvidia.com`) — OpenAI wire format with three quirks that need a dedicated adapter:

1. **Reasoning via `chat_template_kwargs`** — NIM is vLLM-backed; thinking budget is passed as `chat_template_kwargs` rather than a `reasoning` field.
2. **Tool-schema rewrite** — NIM rejects JSON Schema boolean subschemas (`true`/`false` as schemas) and object properties named `type`. `nim_tool_schema.py` rewrites IR tool schemas into a NIM-safe form before sending.
3. Native tool handling via `nim_native_tools.py`.

```yaml
providers:
  nvidia-nim:
    api_key: os.environ/NVIDIA_API_KEY
    base_url: https://integrate.api.nvidia.com/v1   # default
```

## `cline`

Cline (`api.cline.bot`) — OpenAI Chat Completions-compatible with three quirks:

1. **Auth**: WorkOS OAuth tokens sent as `Authorization: Bearer workos:<token>` — the `workos:` prefix is mandatory (auto-prepended when missing).
2. **Fingerprint**: every request carries a client-identification header.
3. **OAuth lifecycle**: tokens refresh on demand; auto-refresh runs as a background service.

```yaml
providers:
  cline:
    client_id: os.environ/CLINE_CLIENT_ID
    client_secret: os.environ/CLINE_CLIENT_SECRET
```

Connect via the admin UI (Providers → Cline → Connect) or the OAuth endpoints (`/admin/cline/oauth/*`). The callback lands on `/cline/oauth/callback`.

## `workbuddy`

WorkBuddy / CodeBuddy (Tencent) — OpenAI Chat Completions-compatible with four quirks (ported from workbuddy2api):

1. **Auth**: the key secret is a WorkBuddy **auth JSON** (nested or flat — `workbuddy_auth.py` normalizes both); requests carry the derived access token.
2. Fingerprint/headers as upstream expects.
3. Auto-refresh background service (shared `CircuitBreaker`/`Backoff` primitives from `wiwi/core/recovery.py`).
4. Account import/export via admin endpoints.

```yaml
providers:
  workbuddy:
    client_id: os.environ/WB_CLIENT_ID
    client_secret: os.environ/WB_CLIENT_SECRET
```

Import accounts via `/admin/workbuddy/import` or the admin UI.

## `opencode`

OpenCode Zen (`opencode.ai/zen`) — a multi-protocol gateway: the same base URL serves **four upstream wire formats, chosen per model** (see the Zen endpoints table). The adapter:

- Routes each model to its correct upstream protocol.
- Refreshes its `opencode/<version>` User-Agent live (`opencode_version.py`) so the upstream sees a current client version.

```yaml
providers:
  opencode:
    api_key: os.environ/OPENCODE_API_KEY
    base_url: https://opencode.ai/zen/v1     # default
```

---

## Key pools & weights

Config gives each provider its primary key. The admin API manages a **key pool** per provider — multiple real upstream credentials, each with:

| Field | Meaning |
|---|---|
| `label` | Identifier for the key (used in URLs, logs, cooldowns) |
| `secret` | The credential (encrypted at rest; reveal endpoint is audit-logged) |
| `weight` | WRR bias when picking among pool keys |
| `enabled` | On/off switch |
| health state | `active` · `cooling` (transient error → cooldown) · `invalid` (auth rejected) · `disabled` |

Router picks a deployment, then a key from that deployment's pool. Errors trigger cooldowns; cooldowns expire automatically (or adaptively with `router_settings.health_model: scored` + `adaptive_cooldown: true`); the optional HealthHealer probes sick keys with 1-token requests to restore them (see [ARCHITECTURE.md](ARCHITECTURE.md) §Recovery).

## Mixing providers in one model group

```yaml
model_list:
  - model_name: claude-sonnet
    wiwi_params:
      model: anthropic/claude-sonnet-4-5
      weight: 2
  - model_name: claude-sonnet          # same group, second deployment
    wiwi_params:
      model: openrouter/anthropic/claude-sonnet-4.5
      weight: 1
```

Clients always request `claude-sonnet`. WRR splits traffic 2:1; upstream failures failover to the other deployment (and to `fallback_groups`, if configured) mid-stream with tape-based resume.

## Provider quirks live in adapters — nowhere else

The binding invariant: all dialect/provider branching stays inside `wiwi/wire/` and `wiwi/providers/`. `core/`, `router/`, `auth/`, `streaming/` must never import dialect or provider symbols. If you find yourself special-casing a provider in the gateway or router, it belongs in the adapter.
