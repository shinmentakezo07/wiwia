# Cache Logic Hardening — Design

Date: 2026-08-21
Status: Approved (verbal), pending implementation
Scope: Enhance the existing provider prompt-cache plumbing so cached-token reporting, hit flags, savings accounting, and Anthropic cache_control all work correctly. No new storage layers.

## Problem

wiwi relays provider-side prompt caching today (verified: `tok_cached=128/149` via OpenRouter), but four gaps make the existing logic incomplete:

1. **Streaming loses cached tokens.** OpenAI/OpenRouter send `usage` in the same final chunk as `choices` + `finish_reason`. `OpenAIAdapter.decode_stream_event` parses usage only under `if not choices:`, so streaming requests drop real usage (including `cached_tokens`) and fall back to estimation.
2. **`cache_hit` is dead.** `RequestContext.cache_hit` flows into logs/SSE but is never assigned — always `False`.
3. **No dollar-savings metric.** CORE.md §6 calls for "$ saved by cached tokens"; `LogEvent` has no field for it.
4. **Anthropic system `cache_control` dropped.** Claude Code sends system blocks with `cache_control: {type: ephemeral}` — the mechanism that enables Anthropic prompt caching. `AnthropicAdapter.encode_request` flattens system to a plain string (`_system_text`), silently disabling caching on Anthropic→Anthropic routes.

## Design

### A. Streaming usage parsing (openai_adapter.py)

Parse `usage` from any chunk that carries it, independent of `choices`. Emit `UsageFinal` once per stream (first occurrence wins; last-wins is equally fine since values converge — choose last-wins for correctness with providers that send cumulative updates). Keep the `[DONE]` → `StreamEnd`, tool-call close logic, and finish-reason mapping unchanged.

### B. `cache_hit` semantics (core/gateway.py)

Define: `cache_hit = True` iff the provider reported `cached_tokens > 0` for the served response. Set in `_price()` and `_price_stream()` alongside cost computation. No changes needed downstream — `LogEvent.cache_hit` and `public_dict` already carry it.

### C. Cache savings in dollars (logging_core/events.py, core/gateway.py)

Add `cache_savings: float = 0.0` to `LogEvent`. Computed in the pricing step:
`savings = cached_tokens × max(0, input_rate − cache_read_rate)` using the model's price entry; `0.0` when unpriced. Exposed via `/admin/logs/requests` and SSE automatically through `public_dict`.

### D. Anthropic system blocks (providers/anthropic_adapter.py)

In `encode_request`, build `system` from system-role messages preserving `cache_control`: if any part carries it, emit `system: [{type: text, text, cache_control}, ...]`; else keep the current joined-string form (max compatibility).

## Non-goals

- Local response cache / exact-match store (user declined).
- Sticky prefix routing / session affinity (deferred; see PLAN.md G13).
- Semantic cache.
- **Redis backends** — deliberately deferred. This scope is stateless per-request computation; Redis adds value only for multi-instance deployments sharing auth-key cache and rate-limiter windows (config's unused `redis_url` field is reserved for that), or a future response cache.

## Testing

Regression tests per item:
- A: chunk with both `choices` (finish_reason) and `usage` yields `UsageFinal` with cached tokens.
- B: pricing a turn with `cached_tokens > 0` sets `ctx.cache_hit`; zero cached leaves it False (non-streaming and stream paths).
- C: known-price model produces expected savings; unknown model yields 0.0; field present in log events.
- D: Anthropic request with system `cache_control` round-trips as block form; without it stays a string.

Full suite + ruff must pass.
