# Fionn / wiwi — End-to-End Bug Audit

**Date:** 2026-08-26
**Baseline:** 532 tests pass, ruff clean. Bugs below exist in paths not covered by the thematic regression suite.

Each finding verified against source by reading the cited lines. Severities: 🔴 critical · 🟠 high · 🟡 medium · ⚪ low.

---

## 🔴 Critical

### 1. Stream pump deadlocks forever if `encode_request` throws before `ready.set()`
**File:** `wiwi/core/gateway.py:284-335` (`_pump_once`)
**Trigger:** Any streaming request whose IR→provider encoding raises (unsupported tool schema, bad content type, `set_tool_context` failure).

`_pump_once` runs `adapter.encode_request` / `set_tool_context` / header building at **lines 292-296 — before the `try:` at line 302**. If any of that raises, the exception propagates out without ever calling `ready.set()`. The caller (`call_one` at line 127) does `await ready.wait()` and **blocks forever**. The `_pump` wrapper's `try/finally` only decrements `dep.inflight` — it does not catch the exception or set `ready`. The outer `except BaseException` in `stream()` never fires because `execute_with_retries` is stuck inside `call_one`, not raising.

**Result:** permanent deadlock, no timeout, leaked task + queue.
**Fix:** wrap the encode phase in `try/except` that sets `err_box[0]` and calls `ready.set()` on failure.

---

## 🟠 High

### 2. Parallel tool calls corrupt `output_index` and emit premature `done` events (Responses surface)
**File:** `wiwi/wire/openai_responses.py:202-325` (`ResponsesStreamEncoder`)

The encoder uses a single `_open_out` counter shared across all tools. On a second `ToolCallOpen` while another tool is open:
- `_close_item()` (line 305) **prematurely closes** the currently-open tool (emitting `output_item.done` with incomplete args).
- `_next_output_index()` advances `_open_out` to the new tool's index.
- Subsequent `ToolCallArgsDelta` for the first tool (line 324) emit with `output_index = self._open_out` → **wrong index** (points at tool 1).
- `ToolCallClose` (line 209 `_close_tool`) reads `idx = self._open_out` instead of a per-tool stored value → **closes at wrong output_index**.

The IR contract explicitly allows interleaved parallel tool calls (`Open(0)→Args(0)→Open(1)→Args(1)→Close(0)→Close(1)`); the Anthropic adapter emits them this way. The existing test `test_responses_encoder_parallel_tool_calls_preserved` only checks `call_id`/`name` containment, never `output_index` correctness — so it passes despite corruption.

**Fix:** store `output_index` per-tool in the `_tools` dict at `ToolCallOpen` time; read it back in `_close_tool`/`ToolCallArgsDelta`. Do not call `_close_item()` on a sibling `ToolCallOpen`.

### 3. Parallel tool calls misroute args / crash (Anthropic surface)
**File:** `wiwi/wire/anthropic_messages.py:243-247`

`ToolCallArgsDelta` handler computes `idx = int(self._open_block.split(":")[1])` — it uses `_open_block` (the **most recently opened** block) instead of `d.index` (the IR tool index). For interleaved parallel calls, args for tool 0 after tool 1 opened are emitted at tool 1's block index. If `_open_block` is `"text"` or `"thinking"` (no `:`), `split(":")[1]` raises **`IndexError`**, crashing the stream.

**Fix:** use `d.index` to track per-tool block indices; maintain a map from IR tool index → Anthropic block index.

### 4. Streaming path does not parse `Retry-After` header
**File:** `wiwi/core/gateway.py:319-331` (streaming pre-data error path)
**Trigger:** streaming request gets 429 (or other retryable status) with `Retry-After`.

The non-streaming path parses `Retry-After` at line 95-97 and sets `err.retry_after`. The streaming path (lines 319-331) calls `error_from_provider_status` but **never parses `Retry-After`** → `err.retry_after` stays `None`. Consequences: key-pool cooldown uses the 30s default instead of the provider value (`router.py:135`); retry sleep uses exponential backoff instead of the provider value (`router.py:489`).

**Fix:** add `ra = _parse_retry_after(resp.headers.get("retry-after"))` and `if ra is not None: err.retry_after = ra` to the streaming error path.

### 5. Resume pump uses unlocked `on_result`, racing with `pick_key`
**File:** `wiwi/core/gateway.py:255-256, 260`

`_attempt_resume` calls `dep.provider.on_result(key, 200, None)` (unlocked variant) for both success and failure. `pick_key` holds `_rr_lock`; `on_result` does not → `key.req_count += 1` races with concurrent `pick_key` reads, losing counts under concurrent resume load on the same provider.

**Fix:** use `on_result_locked` in `_attempt_resume` for both paths.

### 6. Streaming success counted at connect time, not stream completion
**File:** `wiwi/router/router.py:475`
**Trigger:** flaky upstream that accepts connections then drops them mid-stream.

`on_result_locked(key, 200, None)` fires immediately after `call_one` returns — but for streaming, `call_one` returns the pump task at **connect time** (line 134) before any content flows. A key that connects-then-fails repeatedly accumulates `req_count` (successes) that are never undone, appearing healthier than it is.

**Fix:** for streaming, defer `on_result(200)` until the pump completes successfully.

### 7. Deployment excluded from retries after key-pool exhaustion → premature 503
**File:** `wiwi/router/router.py:468-471`
**Trigger:** single-deployment group where all keys hit 429 simultaneously.

`tried_dep_ids.add(id(dep))` runs at line 468 **before** key selection. If `pick_key` returns `None` (all keys cooling), the deployment is already excluded; next iteration `pick_deployment` returns `None` → loop breaks with `WiwiError(503)` even though keys will cool off within `retry_in` seconds.

**Fix:** only add to `tried_dep_ids` when the deployment actually fails (raises `WiwiError`), not when key selection returns `None`.

### 8. Gemini ignores `reasoning_effort='none'` — thinking stays enabled
**File:** `wiwi/providers/gemini_adapter.py:90-92`
**Trigger:** client sends `reasoning_effort='none'` routed to a Gemini provider.

`effective_thinking_budget()` returns `None` for `'none'` → `if thinking_budget is not None` is `False` → `thinkingConfig` is never set → thinking stays at model default. Every other adapter (Anthropic, OpenAI, NIM, OpenRouter) explicitly disables reasoning for `'none'`. Gemini should set `thinkingConfig={"thinkingBudget": 0}`.

### 9. Anthropic cost double-subtracts cached tokens
**File:** `wiwi/cost/pricing.py:49` + `wiwi/providers/anthropic_adapter.py:213`
**Trigger:** any Anthropic response using prompt caching.

Anthropic's `input_tokens` **already excludes** `cache_read_input_tokens`. The adapter maps `input_tokens` → `prompt_tokens` (line 213) and `cache_read_input_tokens` → `cached_tokens` (line 215). Then `cost_with_status` does `uncached_prompt = max(0, prompt_tokens - cached_tokens)` — double-subtracting. When `cache_read > input_tokens`, fresh input is zeroed and billed at **$0**. For OpenAI/Gemini/NIM (where `prompt_tokens` includes cached) the formula is correct.

**Fix:** Anthropic should price `prompt_tokens` at full input rate (not minus cached) + `cached_tokens` at cache-read rate.

### 10. Anthropic cache-creation (cache-write) tokens billed at $0
**File:** `wiwi/cost/pricing.py` (no `cache_creation` field) + `wiwi/core/gateway.py:490-491, 515`

`cache_creation_tokens` (Anthropic cache-write, populated at `anthropic_adapter.py:216`) is tracked in the IR and `UsageFinal`, but **never passed to `cost_with_status`** (gateway calls it with only `prompt/completion/cached`). There is no pricing field for it (`admin_put_pricing` only accepts `cache_read_per_1m`). Anthropic charges cache writes at ~1.25× input — these tokens are billed at **$0** (undercharge).

### 11. Non-WiwiError first-delta exception leaks upstream streaming connection
**File:** `wiwi/server/app.py:497-505`
**Trigger:** a non-`WiwiError`, non-`StopAsyncIteration` exception during `await anext(stream)`.

The streaming first-delta path catches `StopAsyncIteration` and `WiwiError` (both call `stream.acclose()`), but any other exception falls to the outer `except Exception` (line 527) which does **not** close the stream. The `gateway.stream()` generator holds a `pump_task` with an open httpx connection + queue; without `aclose()`, the generator's `finally` may not run promptly.

**Fix:** add a bare `except` that calls `await stream.aclose()` before re-raising.

### 12. Unauthenticated `/metrics` endpoint exposes usage telemetry
**File:** `wiwi/server/app.py:702-706`
**Trigger:** `GET /metrics` with no `Authorization` header when `prometheus_enabled=true`.

The metrics handler has no `is_admin()` guard, unlike every `/admin/*` endpoint. Any unauthenticated client can read `wiwi_requests_total`, `wiwi_tokens_total`, `wiwi_cost_total`, per-provider counts, latency histograms, and TTFT distributions.

**Fix:** add an auth check (master key or a dedicated scrape token), or bind under `/admin/`.

---

## 🟡 Medium

### 13. `_inject_id` stamps a single id across multi-frame SSE chunks, breaking Last-Event-ID resumption
**File:** `wiwi/server/app.py:331-335` + `wiwi/wire/openai_responses.py:265-285` + `wiwi/wire/anthropic_messages.py:220-233`

`_inject_id` prepends one `id: <seq>` line to a chunk that may contain **multiple** SSE frames (Responses encoder joins 2-3 frames per `feed()`; Anthropic joins `content_block_start`+`content_block_delta`). Per the SSE spec, an `id` line sets `last-event-id` for the *next* event dispatched — so the client's `Last-Event-ID` after the chunk points at the first sub-event, not the last. On reconnect the client replays from the wrong offset (re-emitting or skipping events).

**Trigger:** `stream_event_ids=True` with Responses or Anthropic surface, on any delta opening a new content block.
**Fix:** inject the id after each frame's terminating blank line (split on `\n\n`, tag each frame).

### 14. Partial-JSON repair produces invalid JSON on truncated `\uXXXX` escapes
**File:** `wiwi/streaming/partial_json.py:46-66`

`_repair_truncated_json` handles a dangling backslash but not an unterminated `\uXXXX` escape. A fragment split as `{"k":"v\u00` then `41"}` produces `{"k":"v\u00"}` — invalid (`\u00"` needs 4 hex digits). `json.loads` rejects it → `parse_partial` falls back to `({}, False)` — **silent total loss** of tool args.

**Fix:** strip trailing incomplete `\uXXXX` (backslash + 0-3 hex chars) before appending the closing quote.

### 15. Grace-drain queue deadlock when queue fills
**File:** `wiwi/core/gateway.py:415-419`
**Trigger:** client disconnects during a high-volume stream with `stream_grace_drain_s > 0`.

In grace-drain mode the consumer (`stream()`) is cancelled, so nobody calls `queue.get()`. The pump keeps `await queue.put(d)`; once the 4096-slot queue fills, `put` **blocks forever**. The `grace_deadline` check runs only at the top of each iteration (after reading a line), so a stuck `put` never reaches it.

**Fix:** use `put_nowait` with `except QueueFull`, or check `ctx.cancel` before `put`.

### 16. `asyncio.shield(_close_upstream())` can hang if close blocks
**File:** `wiwi/core/gateway.py:445-446`
**Trigger:** client disconnect + wedged upstream transport simultaneously.

`await asyncio.shield(_close_upstream())` inside the `CancelledError` handler blocks forever if `resp_cm.__aexit__` hangs. The original cancellation is never propagated.

**Fix:** wrap in `asyncio.wait_for` with a short timeout.

### 17. 429 causes soft-retry loop burning attempts on the same dead deployment
**File:** `wiwi/router/router.py:483-491`
**Trigger:** single deployment, all keys rate-limited.

On 429, the key cools but `record_fail` is NOT called on the deployment (429 ∉ {408,500,502,503,504,529}). The deployment stays "available" → `pick_deployment` keeps selecting it → `pick_key` keeps returning `None` → retry loop wastes the entire budget sleeping/retrying the same dead deployment, then 503.

**Fix:** `record_fail` on the deployment when all keys are exhausted, or break early when `key is None` for the only available deployment.

### 18. Loop detection misses oscillating loops (A-B-A-B)
**File:** `wiwi/core/gateway.py:406-411`

The counter only catches *exact consecutive* repetition. An oscillating model (`A`,`B`,`A`,`B`…) resets `loop_count` to 1 on every alternation and never reaches `loop_limit`.

**Fix:** track a small window of recent chunks or detect periodicity.

### 19. Chronic slow-fail deployments never cooldown (60s window < failure interval)
**File:** `wiwi/router/router.py:158-164`
**Trigger:** a deployment failing once every >60s forever.

`record_fail` prunes to the last 60s. If failures are spaced >60s apart, `len(recent)` is always 1 and cooldown (`>= allowed_fails`) never triggers.

**Fix:** the 60s window should be ≥ 2×`cooldown_time`, or use a decaying count.

### 20. `validate_tool_args` accepts `bool` for `'number'` schema type
**File:** `wiwi/streaming/validation.py:88-94`

`isinstance(True, (int, float))` is `True` because `bool` subclasses `int`. The bool-as-int guard only fires for `expected == 'integer'`. So a number-typed parameter receiving `true` validates as OK.

**Fix:** add `if expected == 'number' and isinstance(value, bool): return False`.

### 21. OpenAI adapter streaming drops `reasoning` field (only checks `reasoning_content`)
**File:** `wiwi/providers/openai_adapter.py:280`

`decode_stream_event` checks only `delta.get('reasoning_content')`, but `decode_response` checks both `reasoning_content` and `reasoning`. An OpenAI-compatible provider streaming via `delta.reasoning` silently drops `ThinkingDelta`s (NIM and OpenRouter handle both field names).

**Fix:** check both `reasoning_content` and `reasoning` in streaming.

### 22. NIM nested aliased param names never un-aliased in tool responses
**File:** `wiwi/providers/nim_tool_schema.py`

`_alias_in_node` recursively aliases unsafe param names (e.g. `type` → `_nim_arg_type`) at all nesting levels, but `collect_nim_tool_aliases` only scans **top-level** properties and `unalias_nim_tool_args` only un-aliases top-level keys. A nested `type` is aliased in the schema sent to NIM, but the model's returned `_nim_arg_type` in a nested object is never un-aliased → client receives `_nim_arg_type`.

**Fix:** recurse in `collect_nim_tool_aliases` and `unalias_nim_tool_args`.

### 23. `error_from_provider_status` 'tokens' substring heuristic misclassifies 400s
**File:** `wiwi/providers/base.py:86-88`

`status == 400 and 'tokens' in msg.lower()` triggers `context_window_fallback` for errors like "max_tokens must be a positive integer" or "reasoning_tokens is not supported" — wasting a fallback slot and routing to a different model unexpectedly.

**Fix:** narrow the heuristic; drop `'tokens'` from the 400 classification, or require stronger context-window markers.

### 24. Spend accounting inconsistency: non-streaming does NOT suppress `update_spend` failures
**File:** `wiwi/server/app.py:517-519` vs `631-633`

Non-streaming: `await state_.auth.update_spend(...)` is **not** wrapped in `contextlib.suppress` — a transient DB error propagates to the outer `except Exception`, overwrites `ctx.status=500`, and returns a 500 to the client **even though the LLM response was already generated successfully**. The streaming path correctly suppresses it.

**Fix:** wrap non-streaming `update_spend` in `contextlib.suppress(Exception)`.

### 25. Task leak in admin SSE keepalive loop
**File:** `wiwi/server/app.py:823-841`

Each iteration creates `get_task` and `shutdown_task`; `asyncio.wait` returns `(done, _pending)` and **`_pending` is never cancelled**. Every 15s idle period leaks one orphaned task. The `finally` only cancels `fwd_tasks`.

**Fix:** `for t in _pending: t.cancel()` after each `asyncio.wait`.

### 26. `base_url` persistence corrupts on non-string input
**File:** `wiwi/server/app.py:1094-1098` (also `1011`)

`str(_interpolate(body['base_url']))` on a dict/list input yields a truthy `str(dict)` like `"{'nested': True}"` that passes the non-empty check and is stored as `acct.base_url`, breaking all upstream requests.

**Fix:** validate that the resolved `base_url` is a `str` and URL-shaped before storing.

### 27. Models.tsx: `setStrategy` mutation throws `ReferenceError` (TDZ)
**File:** `web/src/pages/Models.tsx:142-155`

`setStrategy` is a `useMutation` whose `mutationFn` references `data` (lines 144, 146), but `data` is declared at line 155 with `const` — **after** the mutation. `const` is in the temporal dead zone; when `setStrategy.mutate(s)` runs (strategy dropdown change), the closure throws `ReferenceError: Cannot access 'data' before initialization`. The strategy dropdown is **completely broken**.

**Fix:** move `const data = query.data!` above the `useMutation`, or read `query.data` inside `mutationFn`.

### 28. Settings.tsx InfoTile uses `<a href>` — broken routing under production base path
**File:** `web/src/pages/Settings.tsx:428-443`

InfoTile renders `<a href={props.to}>` with absolute paths (`/models`, `/providers`). In production the SPA is served at `/admin/ui/` with `BrowserRouter basename=BASE_URL`. A plain `<a>` does a **full browser navigation** to `/models`, which FastAPI doesn't serve → 404. Every other nav element uses `<Link to>`.

**Fix:** use `<Link to={props.to}>`.

### 29. Analytics.tsx: over-broad suffix match shows wrong pricing card
**File:** `web/src/pages/Analytics.tsx:731-743`

`selectedModel.endsWith(p.model_id)` is the buggy clause: selecting `gpt-4o` matches a pricing row named `o`; selecting `claude-3-5-sonnet` matches `sonnet` or even `t`. The wrong model's prices and effective $/1M are displayed.

**Fix:** drop the `selectedModel.endsWith(p.model_id)` clause; the `p.model_id.endsWith(selectedModel)` clause already covers the provider-prefix case.

### 30. OpenRouter streaming drops `reasoning.encrypted` details
**File:** `wiwi/providers/openrouter_adapter.py:213-221`

Streaming `decode_stream_event` handles `reasoning.text` and `reasoning.summary` but drops `reasoning.encrypted` (non-streaming `decode_response` handles all three). Encrypted reasoning is lost in streaming mode.

**Fix:** add `elif rd_type == 'reasoning.encrypted'` emitting `ThinkingDelta(rd.get('data',''), signature=rd.get('id'))`.

---

## ⚪ Low

### 31. Redis TPM limiter uses `zcard` (count) instead of token sum — dormant (Redis not wired)
**File:** `wiwi/ratelimit/redis.py:108-131`

`zcard` returns member count, not sum of token values. A TPM limit of 10000 is enforced as "10000 requests". `record_tokens` never updates Redis sorted sets (only memory fallback). `zadd` member collisions silently lose reservations. *Currently dormant* — `app.py:170` always uses the memory limiter; `redis_url` is read but unused.

### 32. Memory `record_tokens` misattributes actual usage under concurrent same-key requests
**File:** `wiwi/ratelimit/memory.py:90-108`

Replaces the **newest** estimated reservation regardless of which request completed. Out-of-order completion → stale estimate remains, actual count replaces the wrong reservation. No request-id correlation.

### 33. `estimate_tokens` runs blocking tiktoken inside async stream-pump coroutines
**File:** `wiwi/cost/pricing.py:82-93` (called from `gateway.py:425, 481`)

`import tiktoken` + `get_encoding` (disk load) + `encode` (CPU-bound) are all synchronous, blocking the event loop. First-request latency spike + large-prompt stalls.

**Fix:** `asyncio.to_thread` or pre-import + cache encodings at startup.

### 34. `count_tokens` / `list_models` over-reserve RPM rate-limit slots
**File:** `wiwi/server/app.py:668, 684`

Both call `authenticate(...)` with default `reserve=True`, adding an RPM event for endpoints that never make an upstream call. A tight loop on `/v1/models` can exhaust a key's RPM quota and block real completions.

**Fix:** pass `reserve=False` for read-only / non-upstream endpoints.

### 35. Percentile math differs between `stats._p95` and `metrics._percentile`
**File:** `wiwi/server/stats.py:42-44` vs `wiwi/server/metrics.py:17-21`

`stats` uses nearest-rank (`ceil`); `metrics` uses truncation (`int`/`floor`). For `n=20, p=95`: stats → index 18, metrics → index 19. `/admin/stats/overview` and `/metrics` report different p95 for the same data.

### 36. Latency `p95_ms` computed over different populations (in-memory vs DB)
**File:** `wiwi/server/stats.py:73` vs `wiwi/logging_core/db_sink.py:285-289`

In-memory overview includes `latency_ms==0` entries; DB-backed overview excludes them (`WHERE latency_ms > 0`). Same data → different p95 depending on which path answers.

### 37. `replay()` reads deque without the lock
**File:** `wiwi/logging_core/subsystem.py:66-70`

`SSEBroadcastSink.replay()` iterates `self._rings[stream]` without `self._lock` while `publish()` mutates the same deque under the lock. Concurrent publish can raise `RuntimeError: deque mutated during iteration`.

### 38. Audit events have no SSE ring — silently dropped when DB is down
**File:** `wiwi/logging_core/subsystem.py:124-128`

`log_audit` writes directly to DB with no in-memory copy. If `db_sink` is `None`, audit events are silently dropped with no error.

### 39. Metrics provider label not escaped in Prometheus exposition
**File:** `wiwi/server/metrics.py:65-67`

Provider names containing `"` or `\` produce malformed Prometheus text. Provider creation validates non-empty but not for quote/backslash characters.

### 40. Budget projection wildly inflates with < 1 hour of history
**File:** `web/src/pages/BudgetsAlerts.tsx:58-87`

`windowDays` floor is 1/24 (1 hour) → scale factor up to 720×. 5 minutes of traffic extrapolated to a month is absurd.

### 41. WRR starvation after key recovery from cooldown
**File:** `wiwi/router/router.py:96-102`

A recovered key's `current_weight` was frozen (possibly negative); it's starved for several rounds until it catches up.

### 42. Latency-based routing pins all traffic to the first cold deployment
**File:** `wiwi/router/router.py:190-191`

`p95_latency()` returns `0.0` for all cold deployments; `min` ties deterministically return the **first** element → it gets all traffic, starving others.

### 43. ProviderDetail stale-name race after rename
**File:** `web/src/pages/ProviderDetail.tsx:~525-570`

`KeyPoolCard` captures `props.p.name` from the initial render; a key mutation immediately after rename PATCHes against the old name → 404. Small race window (invalidate triggers immediate refetch).

### 44. Settings "copied" state reused for cache-clear → misleading button label
**File:** `web/src/pages/Settings.tsx:268-272`

`clearCache()` sets the same `copied` boolean used by the "Clear key" button → "Clear cache" flips the "Clear key" button label to "Cleared".

### 45. Dashboard live-bucket one-shot seed never re-seeds after SSE disconnect
**File:** `web/src/pages/Dashboard.tsx:240-245`

If SSE disconnects >30 min, the seed effect won't re-run (`liveSeededRef` stays true); `liveRef.current` holds stale buckets → flat-zero sparkline until a brand-new SSE event.

### 46. AdminStreamProvider token captured at mount, not reactive to auth changes
**File:** `web/src/components/Layout.tsx` (via `stream.tsx`)

Stream effect captures `getToken()` at mount with `[]` deps. Mostly mitigated by `RequireAuth` unmounting on logout, but fragile if gating is restructured.

### 47. VirtualKeys edit dialog cannot clear budget/rpm/tpm back to unlimited
**File:** `web/src/pages/VirtualKeys.tsx:405-465`

No "Clear budget" checkbox (unlike the expiry clear checkbox). Once a budget is set, it can only be removed via the Budgets page.

### 48. Responses decoder: `json_object` format not handled + system/developer role misrouted
**File:** `wiwi/wire/openai_responses.py:128-135, 73`

`text.format.type=='json_object'` is not decoded (only `json_schema`) → upstream never instructed to produce JSON. System/developer role messages map to `'user'` instead of `'system'`.

### 49. OpenAI Chat decoder: ThinkingPart placed after TextPart, breaking Anthropic extended-thinking
**File:** `wiwi/wire/openai_chat.py:42-52`

Order is `[TextPart, ThinkingPart, ToolUsePart]`; Anthropic extended-thinking requires the final assistant turn to **begin** with a thinking block → Anthropic rejects the request.

### 50. `expires_at` contract inconsistency (POST `ttl_seconds` vs PATCH absolute epoch)
**File:** `wiwi/auth/service.py:119, 171`

`create_key` takes `ttl_seconds` (relative); `update_key` takes `expires_at` (absolute epoch). Admin extending via PATCH must send an absolute epoch, not a TTL — a footgun.

### 51. ChatStreamEncoder emits empty `reasoning_content` for signature-only deltas
**File:** `wiwi/wire/openai_chat.py:226`

`ThinkingDelta` with empty text (signature-only) emits `{reasoning_content: ''}` — a useless empty-string delta. `ResponsesStreamEncoder` correctly returns `None`.

---

## Summary by severity

| Severity | Count | Notable |
|---|---|---|
| 🔴 Critical | 1 | Stream pump deadlock on encode failure |
| 🟠 High | 11 | Parallel tool-call corruption (×2 surfaces), streaming Retry-After, Anthropic cost bugs, /metrics auth gap |
| 🟡 Medium | 18 | SSE id injection, partial-JSON, grace-drain deadlock, loop detection, UI TDZ + routing |
| ⚪ Low | 21 | Redis limiter (dormant), percentile inconsistency, WRR starvation, doc/contract footguns |
| **Total** | **51** | |

## Top recommendations (fix order)

1. **#1** — stream pump deadlock (wrap encode in try/except + `ready.set()`). Trivial fix, prevents permanent hangs.
2. **#2 + #3** — parallel tool-call encoder corruption (Responses + Anthropic). Core translation contract violation; affects every interleaved tool-call stream.
3. **#9 + #10** — Anthropic cost accounting (double-subtraction + unpriced cache-creation). Direct revenue impact.
4. **#4** — streaming Retry-After. Affects cooldown correctness under 429s.
5. **#7** — deployment exclusion after key exhaustion. Causes premature 503s.
6. **#12** — /metrics auth gap. Security exposure.
7. **#27 + #28** — UI: Models strategy dropdown crash + Settings routing 404. User-visible breakage.
