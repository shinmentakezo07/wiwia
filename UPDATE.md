# UPDATE.md — Translation Layer Fixes & OpenRouter Adapter

> **Session date**: 2026-08-23
> **Status**: All changes verified — 213 tests pass, ruff clean
> **Scope**: OpenAI ↔ Anthropic cross-provider translation fixes, OpenRouter dedicated adapter, multi-turn conversation bug fix

**This document is binding reference material.** Any agent touching the translation layer — OpenAI↔Anthropic cross-provider translation, OpenRouter adapter, multi-turn conversation handling, `reasoning`/`reasoning_effort` mapping, tool_result / `content: null`, `stream_options`, or upstream error extraction — **MUST read this file first** before changing any wire codec (`wiwi/wire/`) or provider adapter (`wiwi/providers/`). The fixes recorded here are the reason the current code behaves correctly; changing those paths without reading this doc is the most common way to silently re-introduce a bug that was already fixed.

Each entry below records the before→after state, the exact files and lines changed, and the tests that cover it. When extending or modifying any of those areas, check this file for existing invariants before writing new code. When a new fix lands in one of these areas, add an entry to this file so a later agent does not rediscover and re-fix the same thing.

---

## Round 96 — OpenRouter repair fidelity and request-schema guards (2026-09-24)

### 96.1 Repaired tool arguments must cross every wire as parsed IR

OpenRouter can truncate a function-call `arguments` string. The provider decoder
repairs the canonical `ToolUsePart.args`; the former wire encoders then preferred
the original `raw_args`, returning or replaying malformed JSON. The OpenAI Chat,
Responses, and OpenCode outbound encoders now serialize `ToolUsePart.args` only.
`raw_args` is not a wire-authoritative copy and must never override repaired IR.

### 96.2 OpenRouter response frames are shape-tolerant, not exception-sensitive

Sync and stream decoding now treat `choices`, `usage`, token details, tool
functions, reasoning detail entries, tool ids/names, and error metadata as
untrusted shapes. Invalid non-text reasoning is omitted, array assistant content
is flattened to its text parts, and non-string metadata cannot break error
message extraction. A malformed optional frame must not become a retryable
gateway error or penalize provider health.

### 96.3 OpenRouter request parameters follow the provider schema

`reasoning.effort` accepts only the documented string enum; typed-wrong or
unknown values are omitted rather than causing a local exception or upstream
schema error. When `reasoning.max_tokens` is not below
`max_completion_tokens`, the completion limit is raised to budget + 1024. A
hosted builtin tool choice is encoded as `{"type":"openrouter:web_search"}`;
a client-defined function with the same name remains an ordinary function
choice.

Sources:
https://openrouter.ai/docs/guides/best-practices/reasoning-tokens,
https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion.md,
https://openrouter.ai/docs/guides/features/server-tools/web-search.

Regression coverage: `tests/test_fix_round96.py`.

---

## TL;DR

Three rounds of fixes:
1. **OpenAI ↔ Anthropic translation enhancements** — 12 fixes to reasoning, tools, thinking, stop reasons, error parsing, and stream handling.
2. **Dedicated OpenRouter adapter** — new `openrouter_adapter.py` with proper `reasoning` parameter translation, `reasoning_details` decoding, and mid-stream error handling.
3. **Multi-turn conversation 400 fix** — `_role_parts_to_content` was dropping thinking blocks, emitting `content: null` on tool-result messages, and leaving empty trailing user messages.

---

## Round 1: OpenAI ↔ Anthropic Translation Enhancements

### 1.1 Anthropic adapter: `max_tokens` must be > `budget_tokens`

**File**: `wiwi/providers/anthropic_adapter.py`

**Issue**: When a client sent `reasoning_effort: "high"` (mapped to 32000 thinking budget) with a small `max_tokens` (e.g. 1000), the Anthropic API rejected the request with `400 invalid_request_error: max_tokens must be greater than thinking.budget_tokens`.

**Before**:
```python
if g.thinking_budget:
    body["thinking"] = {"type": "enabled", "budget_tokens": g.thinking_budget}
elif g.reasoning_effort:
    budget = g.effective_thinking_budget()
    if budget:
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
# max_tokens never adjusted
```

**After**:
```python
thinking_enabled = (g.thinking_budget is not None
                    or (g.reasoning_effort is not None
                        and g.reasoning_effort != "none"))
if thinking_enabled:
    budget = g.effective_thinking_budget()
    if budget is None:
        budget = ir.effort_to_thinking_budget("medium")
    budget = max(budget, MIN_THINKING_BUDGET)  # 1024 min
    if body["max_tokens"] <= budget:
        body["max_tokens"] = budget + 1024  # ensure max_tokens > budget
    body["thinking"] = {"type": "enabled", "budget_tokens": budget}
```

**Tests**: `test_anthropic_max_tokens_raised_above_budget`, `test_anthropic_budget_clamped_to_minimum`, `test_anthropic_max_tokens_unchanged_when_already_above_budget`

---

### 1.2 `reasoning_effort: "none"` disables thinking

**Files**: `wiwi/ir/types.py`, `wiwi/providers/anthropic_adapter.py`, `wiwi/providers/openai_adapter.py`

**Issue**: `reasoning_effort: "none"` (introduced by OpenAI for GPT-5.x models to disable reasoning) was not recognized. The Anthropic adapter would enable thinking with a default budget, and the effort-to-budget mapping returned the "medium" default for unknown values.

**Before**:
```python
_EFFORT_BUDGETS = {"low": 1024, "medium": 8000, "high": 32000}
# "none" not handled → falls through to default "medium"
```

**After**:
```python
_EFFORT_BUDGETS = {"none": None, "low": 1024, "medium": 8000, "high": 32000, "xhigh": 64000}
# "none" returns None → adapters check and skip thinking config
```

**Tests**: `test_anthropic_effort_none_disables_thinking`, `test_openai_effort_none_forwarded`

---

### 1.3 `xhigh` effort level support

**File**: `wiwi/ir/types.py`

**Issue**: OpenAI GPT-5.4+ supports `xhigh` reasoning effort. The IR mapping didn't include it.

**Before**:
```python
_EFFORT_BUDGETS = {"low": 1024, "medium": 8000, "high": 32000}
def thinking_budget_to_effort(budget: int) -> str:
    if budget <= 2048: return "low"
    if budget <= 12000: return "medium"
    return "high"
```

**After**:
```python
_EFFORT_BUDGETS = {"none": None, "low": 1024, "medium": 8000, "high": 32000, "xhigh": 64000}
def thinking_budget_to_effort(budget: int) -> str:
    if budget <= 2048: return "low"
    if budget <= 16000: return "medium"
    if budget <= 48000: return "high"
    return "xhigh"
```

**Tests**: `test_xhigh_effort_maps_to_large_budget`, `test_xhigh_budget_to_effort`

---

### 1.4 `thinking_tokens` captured from Anthropic usage

**File**: `wiwi/providers/anthropic_adapter.py`

**Issue**: Anthropic reports reasoning tokens in `usage.output_tokens_details.thinking_tokens` (both stream and non-stream). The adapter was not capturing this field, so `reasoning_tokens` was always 0 in the IR.

**Before** (non-stream `decode_response`):
```python
turn.usage = ir.Usage(
    prompt_tokens=u.get("input_tokens", 0),
    completion_tokens=u.get("output_tokens", 0),
    cached_tokens=u.get("cache_read_input_tokens", 0),
    cache_creation_tokens=u.get("cache_creation_input_tokens", 0),
    # reasoning_tokens missing
)
```

**After**:
```python
out_details = u.get("output_tokens_details") or {}
turn.usage = ir.Usage(
    prompt_tokens=u.get("input_tokens", 0),
    completion_tokens=u.get("output_tokens", 0),
    cached_tokens=u.get("cache_read_input_tokens", 0),
    cache_creation_tokens=u.get("cache_creation_input_tokens", 0),
    reasoning_tokens=out_details.get("thinking_tokens", 0),
)
```

Same fix applied to stream decoder `message_delta` event.

**Tests**: `test_anthropic_decode_response_captures_thinking_tokens`, `test_anthropic_stream_captures_thinking_tokens`

---

### 1.5 `reasoning_content` in OpenAI `encode_response`

**File**: `wiwi/wire/openai_chat.py`

**Issue**: When a provider returned thinking blocks (e.g. Claude via Anthropic), the OpenAI wire encoder didn't include `reasoning_content` in the response message. OpenAI-shaped clients that look for `reasoning_content` (e.g. OpenRouter, DeepSeek) wouldn't receive the reasoning.

**Before**:
```python
message = {"role": "assistant", "content": turn.text if turn.text else None}
if turn.tool_calls:
    message["tool_calls"] = [...]
# reasoning_content never set
```

**After**:
```python
message = {"role": "assistant", "content": turn.text if turn.text else None}
if turn.tool_calls:
    message["tool_calls"] = [...]
if turn.thinking:
    message["reasoning_content"] = "".join(t.text for t in turn.thinking)
```

**Tests**: `test_openai_encode_response_includes_reasoning_content`, `test_openai_encode_response_no_reasoning_content_when_empty`

---

### 1.6 `reasoning_content` captured from OpenAI-compatible provider responses

**File**: `wiwi/providers/openai_adapter.py`

**Issue**: OpenAI-compatible providers (DeepSeek, OpenRouter, etc.) return reasoning in `message.reasoning_content` or `message.reasoning`. The adapter's `decode_response` didn't capture it.

**Before**:
```python
turn = ir.AssistantTurn(text=message.get("content") or "", raw=data)
# reasoning_content never checked
for tc in message.get("tool_calls") or []:
    ...
```

**After**:
```python
turn = ir.AssistantTurn(text=message.get("content") or "", raw=data)
reasoning = message.get("reasoning_content") or message.get("reasoning")
if reasoning:
    turn.thinking.append(ir.ThinkingPart(reasoning))
for tc in message.get("tool_calls") or []:
    ...
```

**Tests**: `test_openai_decode_response_captures_reasoning_content`, `test_openai_decode_response_captures_reasoning_key`

---

### 1.7 `tool_choice: none` passed through to Anthropic

**File**: `wiwi/providers/anthropic_adapter.py`

**Issue**: `tool_choice: none` was mapped to `{"type": "auto"}` as a workaround for older Anthropic API versions that didn't support `none`. The newer API (2024+) supports `{"type": "none"}` natively.

**Before**:
```python
if isinstance(tc, ir.ToolChoiceNone):
    body["tool_choice"] = {"type": "auto"}  # closest; none unsupported pre-4.x
```

**After**:
```python
if isinstance(tc, ir.ToolChoiceNone):
    body["tool_choice"] = {"type": "none"}
elif isinstance(tc, ir.ToolChoiceAuto):
    body["tool_choice"] = {"type": "auto"}
```

**Tests**: `test_anthropic_tool_choice_none_passed_through`, `test_anthropic_tool_choice_auto_passed_through`

---

### 1.8 `server_tool_use` content blocks handled

**File**: `wiwi/providers/anthropic_adapter.py`

**Issue**: Anthropic's built-in tools (web_search, computer, etc.) return `server_tool_use` content blocks. The adapter only checked for `tool_use`, so server tool calls were silently dropped.

**Before** (non-stream):
```python
elif btype == "tool_use":
    turn.tool_calls.append(...)
# server_tool_use not handled
```

**After** (non-stream):
```python
elif btype == "tool_use":
    turn.tool_calls.append(...)
elif btype == "server_tool_use":
    turn.tool_calls.append(...)  # same handling
```

Same fix in stream decoder `content_block_start`:
```python
if cb.get("type") in ("tool_use", "server_tool_use"):
    self._tool_indices.add(idx)
    out.append(dl.ToolCallOpen(...))
```

**Tests**: `test_anthropic_decode_server_tool_use`, `test_anthropic_stream_server_tool_use`

---

### 1.9 `pause_turn` stop reason mapped

**File**: `wiwi/providers/anthropic_adapter.py`

**Issue**: Anthropic has a `pause_turn` stop reason (used in interleaved thinking). The adapter's stop reason map didn't include it, so it fell through to the default `"stop"` — but only by accident.

**Before**:
```python
{"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
 "tool_use": "tool_call", "refusal": "content_filter"}.get(sr, "stop")
```

**After**:
```python
{"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
 "tool_use": "tool_call", "refusal": "content_filter",
 "pause_turn": "stop"}.get(sr, "stop")
```

Same fix in stream decoder `message_delta` event and non-stream `decode_response`.

**Tests**: `test_anthropic_pause_turn_mapped_to_stop`

---

### 1.10 `output_tokens_details` in Anthropic `encode_response`

**File**: `wiwi/wire/anthropic_messages.py`

**Issue**: When the IR `Usage` has `reasoning_tokens > 0`, the Anthropic wire encoder didn't include `output_tokens_details.thinking_tokens` in the response usage.

**Before**:
```python
"usage": {
    "input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
    "cache_read_input_tokens": u.cached_tokens,
    "cache_creation_input_tokens": u.cache_creation_tokens,
}
```

**After**:
```python
"usage": {
    "input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
    "cache_read_input_tokens": u.cached_tokens,
    "cache_creation_input_tokens": u.cache_creation_tokens,
    "output_tokens_details": {
        "thinking_tokens": u.reasoning_tokens,
    } if u.reasoning_tokens else {},
}
```

**Tests**: `test_anthropic_encode_response_includes_thinking_tokens`, `test_anthropic_encode_response_no_thinking_tokens_details_when_zero`

---

### 1.11 `ChatStreamEncoder._usage`/`_stop` initialization

**File**: `wiwi/wire/openai_chat.py`

**Issue**: `ChatStreamEncoder.__init__` didn't initialize `_usage` and `_stop`. If a stream ended without `UsageFinal` or `Finish` deltas (e.g. an early `StreamError`), `final_frame()` would raise `AttributeError`.

**Before**:
```python
def __init__(self, model, req_id):
    self.model = model
    self.req_id = req_id
    self._started = False
    self._finished = False
    # _usage and _stop only set in feed() when deltas arrive
```

**After**:
```python
def __init__(self, model, req_id):
    self.model = model
    self.req_id = req_id
    self._started = False
    self._finished = False
    self._usage: dl.UsageFinal | None = None
    self._stop: str = "stop"
```

**Tests**: `test_chat_stream_encoder_no_attribute_error_on_final_frame`

---

### 1.12 Error message extraction from nested provider errors

**File**: `wiwi/providers/base.py`

**Issue**: OpenRouter wraps errors as `{"error": {"message": "Provider returned error", "metadata": {"raw": "context length exceeded", "provider_name": "Stealth"}}}`. The error parser used the raw body text, so clients saw the useless top-level message instead of the actual failure reason.

**Before**:
```python
def error_from_provider_status(status, body_text, provider):
    msg = body_text[:500] or f"{provider} returned HTTP {status}"
    # context window check ran against body_text (raw JSON)
```

**After**:
```python
def _extract_error_message(body_text: str) -> str:
    """Extract the most useful message from a provider error body."""
    try:
        data = json.loads(body_text)
    except (json.JSONDecodeError, ValueError):
        return body_text[:500]
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg:
            meta = err.get("metadata") or {}
            raw = meta.get("raw")
            if isinstance(raw, str) and raw and raw != msg:
                return f"{msg} ({meta.get('provider_name', 'upstream')}: {raw})"
            return msg
    ...
```

Context window heuristic now runs against the extracted message.

**Tests**: `test_error_extraction_openrouter_nested`, `test_error_extraction_openai_shape`, `test_error_extraction_anthropic_shape`, `test_error_extraction_plain_text`, `test_error_context_window_detection_with_extracted_msg`

---

### 1.13 `reasoning_effort` guard for `openai-compatible` endpoints

**Files**: `wiwi/providers/openai_adapter.py`, `wiwi/core/gateway.py`

**Issue**: `reasoning_effort` was unconditionally forwarded to all OpenAI-compatible endpoints. Many OpenRouter models (and other compatible backends) reject this field with a 400.

**Before**:
```python
if g.reasoning_effort:
    body["reasoning_effort"] = g.reasoning_effort
# always forwarded
```

**After**:
```python
ptype = deployment_params.get("provider_type")
is_native_openai = ptype != "openai-compatible"
if g.reasoning_effort:
    if is_native_openai:
        body["reasoning_effort"] = g.reasoning_effort
elif g.thinking_budget is not None:
    effort = g.effective_reasoning_effort()
    if effort and is_native_openai:
        body["reasoning_effort"] = effort
```

Gateway updated to pass `provider_type` through `deployment_params`:
```python
params = {"max_tokens": dep.max_tokens, "extra_body": {},
          "drop_params": self.drop_params,
          "provider_type": dep.provider.provider_type}
```

**Tests**: `test_reasoning_effort_not_forwarded_to_openai_compatible`, `test_reasoning_effort_forwarded_to_native_openai`, `test_thinking_budget_not_mapped_for_openai_compatible`

---

### 1.14 `stream_options` only sent when client explicitly asks

**Files**: `wiwi/ir/types.py`, `wiwi/wire/openai_chat.py`

**Issue**: The IR defaulted `stream_options_include_usage` to `True`, and the OpenAI chat codec had a fallback that set it to `True` whenever `stream: true` was present. This meant every streaming request through the Anthropic dialect sent `stream_options: {"include_usage": true}` to OpenRouter, even though the client never asked for it. Some providers reject this.

**Before**:
```python
# ir/types.py
stream_options_include_usage: bool = True  # G4

# wire/openai_chat.py
stream_options_include_usage=bool(stream_opts.get("include_usage",
                                                  bool(body.get("stream"))))
```

**After**:
```python
# ir/types.py
stream_options_include_usage: bool = False  # G4: only when client asks

# wire/openai_chat.py
stream_options_include_usage=bool(stream_opts.get("include_usage", False))
```

**Tests**: `test_stream_options_not_sent_by_default`, `test_stream_options_sent_when_explicitly_requested`, `test_anthropic_dialect_stream_no_stream_options`

---

## Round 2: Dedicated OpenRouter Adapter

### 2.1 New file: `wiwi/providers/openrouter_adapter.py`

**Issue**: OpenRouter was handled by the generic `openai-compatible` adapter, which doesn't translate OpenRouter's `reasoning` parameter, doesn't decode `reasoning_details` arrays, and doesn't handle mid-stream errors with `finish_reason: "error"`.

**Solution**: Created `OpenRouterAdapter(OpenAIAdapter)` that extends the OpenAI adapter with:

| Feature | Translation |
|---|---|
| `reasoning_effort: "high"` | `reasoning: {"effort": "high"}` |
| `reasoning_effort: "none"` | `reasoning: {"enabled": false}` |
| `thinking_budget: 10000` | `reasoning: {"max_tokens": 10000}` (clamped to 1024 min) |
| `max_tokens` | `max_completion_tokens` (deprecated → preferred) |
| `reasoning_details[]` (non-stream) | `ThinkingPart` each (text/summary/encrypted) |
| `delta.reasoning` (stream) | `ThinkingDelta` |
| `delta.reasoning_details[]` (stream) | `ThinkingDelta` with signature |
| Top-level `error` + `finish_reason: "error"` | `StreamError` |
| `: OPENROUTER PROCESSING` SSE comments | Already handled by `LineSSEParser` |

### 2.2 Registry updated

**File**: `wiwi/providers/registry.py`

**Before**:
```python
def get_adapter(provider_type: str) -> ProviderAdapter:
    if provider_type == "anthropic":
        return AnthropicAdapter()
    if provider_type == "gemini":
        return GeminiAdapter()
    return OpenAIAdapter()  # openai + openai-compatible
```

**After**:
```python
def get_adapter(provider_type: str) -> ProviderAdapter:
    if provider_type == "anthropic":
        return AnthropicAdapter()
    if provider_type == "gemini":
        return GeminiAdapter()
    if provider_type == "openrouter":
        return OpenRouterAdapter()
    return OpenAIAdapter()
```

### 2.3 Config updated

**File**: `wiwi/config.py`

Added `"openrouter"` to the `Literal` type for `ProviderDef.provider`:
```python
provider: Literal["openai", "anthropic", "gemini", "openai-compatible", "openrouter"]
```

**File**: `wiwi.yaml`

Changed provider type from `openai-compatible` to `openrouter`.

**File**: `wiwi.yaml.example`

Added OpenRouter example provider block.

**Tests**: 18 tests in `tests/test_openrouter_adapter.py`

---

## Round 3: Multi-Turn Conversation 400 Fix

### 3.1 `_role_parts_to_content` — thinking blocks dropped

**File**: `wiwi/providers/openai_adapter.py`

**Issue**: When Claude Code sent a multi-turn conversation with `thinking` blocks in the assistant's previous response, the `ThinkingPart` was not handled in `_role_parts_to_content`, so the reasoning context was silently lost.

**Before**:
```python
for p in m.parts:
    if isinstance(p, ir.TextPart): ...
    elif isinstance(p, ir.ImagePart): ...
    elif isinstance(p, ir.ToolUsePart): ...
    # ThinkingPart not handled → silently dropped
```

**After**:
```python
for p in m.parts:
    if isinstance(p, ir.TextPart): ...
    elif isinstance(p, ir.ImagePart): ...
    elif isinstance(p, ir.ToolUsePart): ...
    elif isinstance(p, ir.ThinkingPart):
        reasoning += p.text
    elif isinstance(p, ir.ToolResultPart) and m.role == "user":
        # Anthropic convention: tool results as user messages → OpenAI tool role
        out.append({"role": "tool", "tool_call_id": p.tool_use_id,
                    "content": tool_content})
        emitted_tool_results = True
# ...
if reasoning and m.role == "assistant":
    msg["reasoning"] = reasoning
```

### 3.2 `_role_parts_to_content` — `content: null` on tool-result messages

**Issue**: When Claude Code sent tool results (Anthropic convention: `user`-role with `tool_result` content blocks), the adapter emitted `{"role": "user", "content": null}`. OpenRouter rejected this with a 400.

**Before**: ToolResultPart on user-role messages was not handled → `content` stayed `None` → `{"role": "user", "content": null}`.

**After**: ToolResultPart on user-role messages emits `{"role": "tool", "tool_call_id": ..., "content": ...}`. If the entire message was consumed as tool results, the empty trailing user message is skipped.

### 3.3 `_role_parts_to_content` — empty trailing user message

**Issue**: After converting ToolResultParts to `tool`-role messages, an empty `user`-role message with `content: null` or `content: ""` was left behind.

**Before**: Always appended `msg` even if `content` was `None` and no `tool_calls`.

**After**:
```python
if emitted_tool_results and content is None and not tool_calls:
    continue  # skip empty trailing message
```

**Tests**: `test_multi_turn_thinking_preserved`, `test_multi_turn_tool_result_no_empty_user_message`, `test_multi_turn_no_null_content_without_tool_calls`, `test_multi_turn_tool_result_with_text`

---

## Round 4: Performance & Property-Based Testing

### 4.1 ORJSONResponse for all API/admin routes

**File**: `wiwi/server/app.py`

**Issue**: FastAPI's default `JSONResponse` uses stdlib `json` for serialization. wiwi already depends on `orjson` (used in the streaming hot path), but admin/ API responses were still serialized with the slower stdlib encoder.

**Before**:
```python
from fastapi.responses import JSONResponse, StreamingResponse
# ...
return JSONResponse(body, status_code=status, ...)
```

**After**:
```python
from fastapi.responses import ORJSONResponse, StreamingResponse
# ...
return ORJSONResponse(body, status_code=status, ...)
```

All ~25 `JSONResponse(...)` calls in `app.py` replaced with `ORJSONResponse(...)`. This covers error responses, admin API endpoints, model lists, provider CRUD, stats, logs, and the chat completions success path.

### 4.2 Hypothesis property-based tests for IR round-trip and delta legality

**File**: `tests/test_property_roundtrip.py` (NEW)

**Issue**: The hand-written tests in `test_translation_enhancements.py` and `test_openrouter_adapter.py` only cover specific cases. Property-based testing with Hypothesis generates hundreds of random inputs to verify invariants across the entire input space — exactly the class of bugs that the manual fixes addressed.

**Properties tested**:

| # | Property | What it catches |
|---|---|---|
| 1 | OpenAI encode → decode preserves user/assistant text | Text loss during round-trip |
| 2 | Anthropic encode → decode preserves user/assistant text | Text loss during round-trip |
| 3 | No `content: null` without `tool_calls` in OpenAI body | The multi-turn 400 bug |
| 4 | OpenRouter body never contains `reasoning_effort` | Leaked OpenAI-native param |
| 5 | OpenRouter uses `max_completion_tokens`, not `max_tokens` | Deprecated field usage |
| 6 | ChatStreamEncoder produces well-formed SSE for any legal delta sequence | Malformed streaming output |

**Dependency**: `hypothesis>=6.100` added to `dev` extras in `pyproject.toml`.

---

## Files Changed Summary (all rounds)

| File | Changes |
|---|---|
| `wiwi/ir/types.py` | `none`/`xhigh` effort levels; `stream_options_include_usage` default `False`; `effective_thinking_budget()` returns `None` for `none` |
| `wiwi/providers/anthropic_adapter.py` | `max_tokens > budget_tokens`; budget clamped to 1024; `none` disables thinking; `tool_choice: none`; `server_tool_use`; `pause_turn`; `thinking_tokens` capture |
| `wiwi/providers/openai_adapter.py` | `reasoning_content` capture (decode + encode); `reasoning_effort` guard for `openai-compatible`; `ThinkingPart` → `reasoning` field; `ToolResultPart` on user-role → tool messages; skip empty messages |
| `wiwi/providers/openrouter_adapter.py` | **NEW** — dedicated adapter with `reasoning` param, `reasoning_details`, mid-stream errors, `max_completion_tokens` |
| `wiwi/providers/registry.py` | `openrouter` → `OpenRouterAdapter` |
| `wiwi/providers/base.py` | `_extract_error_message()` for nested OpenRouter/OpenAI/Anthropic error shapes |
| `wiwi/wire/openai_chat.py` | `reasoning_content` in `encode_response`; `_usage`/`_stop` init in `__init__`; `stream_options_include_usage` default `False` |
| `wiwi/wire/anthropic_messages.py` | `output_tokens_details.thinking_tokens` in `encode_response` |
| `wiwi/core/gateway.py` | Pass `provider_type` through `deployment_params` |
| `wiwi/config.py` | `"openrouter"` added to provider `Literal` type |
| `wiwi/server/app.py` | All `JSONResponse` → `ORJSONResponse` (orjson serialization for admin/API routes) |
| `wiwi.yaml` | Provider type changed to `openrouter` |
| `wiwi.yaml.example` | OpenRouter example added |
| `pyproject.toml` | `hypothesis>=6.100` added to dev dependencies |
| `tests/test_translation_enhancements.py` | **NEW** — 39 tests covering all Round 1 + 3 fixes |
| `tests/test_openrouter_adapter.py` | **NEW** — 22 tests covering Round 2 + multi-turn |
| `tests/test_property_roundtrip.py` | **NEW** — 6 Hypothesis property-based tests (Round 4) |

**Total: 219 tests pass, ruff clean.**

---

## Round 5: Tool-Call Schema Translation (Latest OpenAI + Anthropic Docs)

> **Session date**: 2026-08-24
> **Status**: All changes verified — 400 tests pass, ruff clean
> **Scope**: Tool-call schema gaps found by reading the latest official OpenAI and Anthropic docs (tool_choice, disable_parallel_tool_use, strict, input_examples, cache_control)

After reading the latest OpenAI Function Calling guide and Anthropic Tool Use / Define Tools / Parallel Tool Use docs, the following gaps were identified and fixed. These are all cross-provider translation bugs: a client using one dialect would silently lose settings when routed to the other provider.

### 5.1 Anthropic wire codec: `tool_choice` "auto" and "none" silently dropped

**File**: `wiwi/wire/anthropic_messages.py`

**Issue**: The Anthropic wire codec's `decode_request` only handled `tool_choice` types `any` and `tool`. When an Anthropic client sent `{"type": "auto"}` (the default) or `{"type": "none"}`, the `tool_choice` was silently set to `None`, so the IR lost the explicit instruction and the provider adapter would not forward it.

**Before**:
```python
tc_raw = body.get("tool_choice") or {}
tool_choice: ir.ToolChoice | None = None
if isinstance(tc_raw, dict):
    if tc_raw.get("type") == "any":
        tool_choice = ir.ToolChoiceRequired()
    elif tc_raw.get("type") == "tool":
        tool_choice = ir.ToolChoiceNamed(tc_raw.get("name", ""))
    # "auto" and "none" → silently dropped (tool_choice stays None)
```

**After**:
```python
if isinstance(tc_raw, dict):
    tc_type = tc_raw.get("type")
    if tc_type == "any":
        tool_choice = ir.ToolChoiceRequired()
    elif tc_type == "tool":
        tool_choice = ir.ToolChoiceNamed(tc_raw.get("name", ""))
    elif tc_type == "auto":
        tool_choice = ir.ToolChoiceAuto()
    elif tc_type == "none":
        tool_choice = ir.ToolChoiceNone()
```

**Tests**: `test_anthropic_decode_tool_choice_auto`, `test_anthropic_decode_tool_choice_none`

### 5.2 `disable_parallel_tool_use` translation (OpenAI ↔ Anthropic)

**Files**: `wiwi/ir/types.py`, `wiwi/wire/openai_chat.py`, `wiwi/wire/openai_responses.py`, `wiwi/wire/anthropic_messages.py`, `wiwi/providers/anthropic_adapter.py`, `wiwi/providers/openai_adapter.py`

**Issue**: Anthropic controls parallel tool use via `disable_parallel_tool_use` inside the `tool_choice` object (e.g. `{"type": "auto", "disable_parallel_tool_use": true}`). OpenAI uses `parallel_tool_calls: false`. Neither direction was translated. An OpenAI client sending `parallel_tool_calls: false` routed to Anthropic would lose the setting (and vice versa).

**Changes**:

1. **IR** (`GenParams`): Added `disable_parallel_tool_use: bool | None = None` field.

2. **OpenAI wire codecs** (Chat + Responses): Decode `parallel_tool_calls: false` into `disable_parallel_tool_use=True`:
```python
disable_parallel_tool_use=(True if body.get("parallel_tool_calls") is False else None),
```

3. **Anthropic wire codec**: Decode `disable_parallel_tool_use` from inside `tool_choice`:
```python
disable_parallel = tc_raw.get("disable_parallel_tool_use")
# ... wired into GenParams(disable_parallel_tool_use=disable_parallel)
```

4. **Anthropic adapter**: Forward `disable_parallel_tool_use` into the `tool_choice` object. When no explicit `tool_choice` was set but `disable_parallel_tool_use` is, use `{"type": "auto"}` as the carrier:
```python
if tc_obj is not None:
    if disable is not None:
        tc_obj["disable_parallel_tool_use"] = disable
    body["tool_choice"] = tc_obj
elif disable is not None:
    body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": disable}
```

5. **OpenAI adapter**: Map `disable_parallel_tool_use=True` → `parallel_tool_calls=false`:
```python
if g.disable_parallel_tool_use is not None:
    body["parallel_tool_calls"] = not g.disable_parallel_tool_use
```

**Tests**: 12 tests covering decode, encode, and cross-provider round-trip in both directions.

### 5.3 `strict` on tool definitions (cross-provider forwarding)

**Files**: `wiwi/providers/anthropic_adapter.py`, `wiwi/providers/openai_adapter.py`

**Issue**: Both OpenAI (structured outputs) and Anthropic (strict tool use) support `strict: true` on tool definitions, but neither adapter forwarded it when encoding tools for the other provider. An OpenAI client sending `strict: true` routed to Anthropic would lose it.

**Anthropic adapter** (after building tools list):
```python
for i, t in enumerate(req.tools):
    if t.strict is not None:
        body["tools"][i]["strict"] = t.strict
```

**OpenAI adapter** (after building tools list):
```python
for i, t in enumerate(req.tools):
    if t.strict is not None:
        body["tools"][i]["function"]["strict"] = t.strict
```

Both adapters omit the key entirely when `strict is None` (preserving the provider default).

**Tests**: `test_anthropic_encode_forwards_strict`, `test_openai_encode_forwards_strict`, `test_cross_provider_openai_to_anthropic_strict`, `test_cross_provider_anthropic_to_openai_strict`

### 5.4 Anthropic `input_examples` on tool definitions

**Files**: `wiwi/ir/types.py`, `wiwi/wire/anthropic_messages.py`, `wiwi/providers/anthropic_adapter.py`

**Issue**: Anthropic's `input_examples` field (array of example input objects for a tool) was not decoded from Anthropic requests, not carried in the IR, and not forwarded by the Anthropic adapter.

**IR** (`Tool`): Added `input_examples: list[dict[str, Any]] | None = None`.

**Anthropic wire codec**: Decoded into IR:
```python
ir.Tool(..., input_examples=t.get("input_examples"))
```

**Anthropic adapter**: Forwarded on encode:
```python
if t.input_examples is not None:
    body["tools"][i]["input_examples"] = t.input_examples
```

**Tests**: `test_anthropic_decode_tool_input_examples`, `test_anthropic_encode_forwards_input_examples`, `test_cross_provider_anthropic_input_examples_round_trip`

### 5.5 Anthropic `cache_control` on tool definitions

**Files**: `wiwi/ir/types.py`, `wiwi/wire/anthropic_messages.py`, `wiwi/providers/anthropic_adapter.py`

**Issue**: Anthropic supports `cache_control: {"type": "ephemeral"}` on tool definitions to set a prompt-cache breakpoint. This was not decoded or forwarded.

**IR** (`Tool`): Added `cache_control: CacheControl = None`.

**Anthropic wire codec**: Decoded into IR:
```python
ir.Tool(..., cache_control=t.get("cache_control"))
```

**Anthropic adapter**: Forwarded on encode:
```python
if t.cache_control is not None:
    body["tools"][i]["cache_control"] = t.cache_control
```

**Tests**: `test_anthropic_decode_tool_cache_control`, `test_anthropic_encode_forwards_cache_control`, `test_cross_provider_anthropic_cache_control_round_trip`

### 5.6 OpenAI Chat/Responses codecs: decode `strict` from tool definitions

**Issue**: Both codecs already decoded `strict` from function tool definitions, but the test suite didn't explicitly verify it. Added regression tests.

**Tests**: `test_openai_chat_decode_strict`, `test_openai_responses_decode_strict`

### Files Changed (Round 5)

| File | Changes |
|---|---|
| `wiwi/ir/types.py` | `Tool`: added `input_examples`, `cache_control` fields. `GenParams`: added `disable_parallel_tool_use` |
| `wiwi/wire/anthropic_messages.py` | Decode `auto`/`none` tool_choice; decode `disable_parallel_tool_use`; decode `strict`/`input_examples`/`cache_control` from tools |
| `wiwi/wire/openai_chat.py` | Decode `parallel_tool_calls=false` into `disable_parallel_tool_use=True` |
| `wiwi/wire/openai_responses.py` | Same `parallel_tool_calls=false` decode |
| `wiwi/providers/anthropic_adapter.py` | Forward `strict`/`input_examples`/`cache_control` on tools; forward `disable_parallel_tool_use` in `tool_choice` |
| `wiwi/providers/openai_adapter.py` | Forward `strict` on tool defs; map `disable_parallel_tool_use` to `parallel_tool_calls` |
| `tests/test_tool_translation_round2.py` | **NEW** — 37 tests covering all Round 5 fixes |

**Total: 400 tests pass, ruff clean.**

---

# Round 6 — 2026 API Alignment (2026-09-02)

> **Session date**: 2026-09-02
> **Status**: All changes verified — 1104 tests pass, ruff clean
> **Scope**: Streaming/state correctness, decode robustness, Anthropic structured-outputs GA, 2026 parameter surface, multimodal wiring

Six groups of work aligning the OpenAI ↔ Anthropic translation layer with the 2026 API surfaces. Regression tests: `tests/test_fix_round24.py` (C1+C2 items). Capability tests: `tests/test_translation_enhancements.py` (C3–C6 items).

## 6.1 Streaming/state correctness (commit 381e5d4)

**OpenAI usage semantics** — `wire/openai_chat.py`, `server/app.py`: `ChatStreamEncoder` now takes `include_usage`; usage is only emitted when the client sent `stream_options.include_usage=true`, and then as a separate final chunk with an **empty `choices` array** after the finish_reason chunk (OpenAI spec shape). Previously usage rode the finish chunk unconditionally.

**Pending thinking signature** — `wire/anthropic_messages.py`: `AnthropicStreamEncoder` tracks `_last_think_idx`; a pending `signature_delta` is flushed against that index when a new thinking block opens or at stream end, instead of being dropped (which would hard-400 the next turn's thinking replay) or landing in the wrong block.

**Responses `response.completed` output array** — `wire/openai_responses.py`: the terminal event now carries the complete `output` array (closed item payloads accumulated through the stream). Codex CLI breaks without it.

**`response.incomplete` on length** — `wire/openai_responses.py`: `Finish(stop_reason="length")` produces the terminal event `response.incomplete` with `status: "incomplete"` + `incomplete_details: {"reason": "max_output_tokens"}` (both stream and non-stream paths).

**Responses event names** — `response.reasoning_summary_text.delta/done` (was `response.reasoning_text.*`); `response.output_text.done` + `response.content_part.done` emitted before `output_item.done`.

**`stop_sequence` round-trip** — `ir/types.py` (`Finish.stop_sequence`, `AssistantTurn.stop_sequence`), `streaming/deltas.py`, both Anthropic decode/encode paths: the matched stop sequence is no longer hardcoded `None`.

**`server_tool_use` history** — `wire/anthropic_messages.py`: echoed `server_tool_use` blocks decode to `ToolUsePart` and the `*_tool_result` family (web_search/code_execution/mcp/computer/browser) decodes to `ToolResultPart`, so server-tool history stays balanced instead of the whole turn being dropped.

**Adapter reset contract** — `providers/anthropic_adapter.py`: `reset()` now clears the pending-usage fields too (`_pending_prompt`/`_pending_cached`/`_pending_cache_creation`).

**Empty text blocks** — `providers/anthropic_adapter.py`: empty `TextPart`s are skipped (Anthropic 400s on `"text": ""`).

## 6.2 Decode robustness (commit 381e5d4)

Non-dict content items are skipped (no 500); string `thinking` values ignored; `max_tokens=0` no longer falls through to `max_completion_tokens`; `developer` role unified to `system` in the Chat codec; Responses args-without-open dropped instead of synthesizing a phantom item; legacy `function_call` finish reason → `tool_call`; `message.refusal` captured into `turn.text`; `compaction` stop reason mapped.

## 6.3 Anthropic structured outputs GA (commit 608f9e1)

`providers/anthropic_adapter.py` + `wire/anthropic_messages.py`: `ResponseFormat(type="json_schema")` now rides **natively** as `body["output_config"] = {"format": {"type": "json_schema", "schema": …, "name": …, "strict": …}}` (2026 GA shape, no beta header) instead of prompt-injecting a JSON-schema instruction into the system prompt. `json_object` keeps the instruction (no native equivalent). The Anthropic codec also decodes `output_config.format` back into IR `ResponseFormat`, so Anthropic-inbound clients get native `response_format` when routed to OpenAI upstreams.

## 6.4 2026 parameter surface (commit 068330a)

- `GenParams.top_k` — decoded from Anthropic, encoded natively by the Anthropic adapter, ignored by OpenAI.
- `GenParams.thinking_type` — `adaptive` encodes as `{"type": "adaptive"}` (no budget_tokens); `disabled` omits thinking and sets `reasoning_effort="none"` (so OpenAI upstreams disable reasoning); `enabled` keeps the budget path.
- Anthropic extras passthrough — the codec captures known-safe 2026 top-level params (`service_tier`, `speed`, `metadata`, `mcp_servers`, `container`, `context_management`, `fallbacks`, `cache_control`) into `req.extras`; the adapter forwards them through `_ANTHROPIC_STANDARD` honoring `drop_params`.
- OpenAI `_STANDARD` grows the 2026 params: `verbosity`, `web_search_options`, `prediction`, `store`, `metadata`, `prompt_cache_key`, `safety_identifier`, `modalities`, `audio`, `logit_bias`, `service_tier`.
- Effort map: `minimal` (1024 floor) and `max` (64000 cap) added; inverse keeps pre-existing boundaries for the collision values.

## 6.5 Multimodal wiring (commit a821230)

- Anthropic **document blocks** (base64/url sources, `title`, `context`) ↔ `ir.DocumentPart`, round-tripping through the Anthropic adapter; other adapters drop them safely.
- OpenAI **`input_audio`** parts → `ir.AudioPart`.
- Anthropic image **`source.type=file`** carries `file_id` on `ImagePart` (Anthropic→Anthropic passthrough).
- **Multimodal tool results**: `ToolResultPart.images` — both codecs collect image blocks from tool-result content; the Anthropic adapter re-emits block-form `tool_result` content (text + image blocks), the OpenAI adapter emits content-parts form. Fixes Claude Code "tool result with screenshot" flows.

## 6.6 Known limitations (unchanged, documented)

- `message_start` usage zeros in the Anthropic stream encoder: real usage only arrives at `UsageFinal`.
- `cache_creation_tokens` has no standard field in OpenAI usage; stays Anthropic-surface-only.
- `previous_response_id` stays rejected (MVP scope).

## Round 7 — parallel tool-call encoder integrity (2026-09-02)

Items #1–#4 of the AUDIT register, re-verified against current source.

### 7.1 Already fixed, now pinned by tests

- **#1 stream-pump deadlock** (`core/gateway.py` `_pump_once`): the IR→provider
  encode phase is wrapped in `try/except` that fills `err_box[0]` and calls
  `ready.set()`, so `call_one` cannot block forever on `ready.wait()`. Fixed in
  `84b084a`; previously untested — pinned by
  `test_stream_pump_survives_encode_failure` (a 2s `wait_for` fails loudly if
  the guard regresses).
- **#4 streaming `Retry-After`** (`core/gateway.py`): the streaming error path
  parses `Retry-After` and sets `err.retry_after`, so key-pool cooldown and
  retry backoff use the provider's value instead of the 30s default. Fixed in
  `84b084a`; pinned by `test_stream_error_path_parses_retry_after`.
- **#2 / #3 parallel tool-call routing**: both encoders already key per-tool
  state by IR index. `ResponsesStreamEncoder` stores `output_index` at
  `ToolCallOpen` and reads it back in `_close_tool`/`ToolCallArgsDelta`; a
  sibling `ToolCallOpen` no longer closes the already-open tool.
  `AnthropicStreamEncoder` resolves the block index from `_tool_blocks[d.index]`
  (not `_open_block`) and drops an orphan `ArgsDelta` instead of raising
  `IndexError` on `"text".split(":")[1]`.

### 7.2 New bug found and fixed — duplicate `output_item.done`

`ResponsesStreamEncoder._close_item()` emitted `output_item.done` for the
currently-open tool but **read the entry without popping it**, leaving the tool
recorded as open in `self._tools`. A later `ToolCallClose` for that index
therefore emitted a **second** `output_item.done` at the same `output_index`.

Reachable whenever a `TextDelta`/`ThinkingDelta` arrives while two tool calls
are open: the interleave closes tool 1, then `ToolCallClose(1)` closes it
again. Observed sequence:

```
output_item.done  output_index=1   <- emitted by the interleave
output_item.done  output_index=0
output_item.done  output_index=1   <- duplicate, from ToolCallClose(1)
```

A duplicate `output_item.done` makes Codex CLI count a phantom tool call.
Fix — `_close_item` delegates to `_close_tool`, which pops:

```python
# kind == "tool"
if self._open_tool is None:
    self._item_open = None
    return []
return self._close_tool(self._open_tool)
```

`_close_tool` was already correct (it pops) and already idempotent for an
unknown index, so the delegate is also safe when the interleave and the
explicit close race.

**Files changed:** `wiwi/wire/openai_responses.py` (`_close_item`)
**Tests:** `tests/test_fix_round25.py`

---

## Round 8 — built-in web search tool translation (2026-09-02)

> **Status**: all changes verified — 1170 tests pass, ruff clean
> **Scope**: cross-provider translation of provider-hosted web search:
> Anthropic `web_search_20250305` ↔ OpenAI Responses `web_search` ↔ OpenRouter
> `openrouter:web_search` ↔ Gemini `google_search`. Citations/annotations stay
> **round 2** — this round ships the tool itself.

Clients could ask for hosted web search before this round, but the request
mangled or vanished in translation. Three loss points, all fixed:

1. **Anthropic surface mangled the tool.** `wire/anthropic_messages.py` decoded
   `{"type": "web_search_20250305", "name": "web_search", ...}` as a *function*
   tool — upstream Anthropic received a broken
   `{"name": "web_search", "input_schema": {"type": "object"}}` custom tool.
2. **Responses surface silently dropped it.** `wire/openai_responses.py` kept
   only `type == "function"`, so a Codex-shaped `{"type": "web_search"}` never
   reached any provider.
3. **Gemini / OpenRouter never saw it.** No provider encoded a builtin search
   tool at all — unreachable from any surface.

### 8.1 IR: builtin discriminator + result block type

**Files**: `wiwi/ir/types.py`, **new** `wiwi/ir/builtin_tools.py`

`Tool` gains `builtin: str | None` + `builtin_config: dict | None` — a string
discriminator, not a type enum: a future `code_execution` builtin adds a
registry row, not a dataclass variant. `ToolResultPart.block_type: str =
"tool_result"` preserves the original Anthropic result-block type so replayed
history re-emits `web_search_tool_result` (not `tool_result`). `ToolUsePart`
gets no new field — builtin-ness is recoverable via `is_builtin_name(name)`.

`wiwi/ir/builtin_tools.py` is the neutral registry (the hub both `wire/` and
`providers/` may import; keeps the layering rule): canonical↔per-surface type
maps, reverse maps (the Anthropic reverse accepts any `web_search_*` prefix —
20250305/20260209/20260318 and future versions), config keys
`("max_uses", "allowed_domains", "blocked_domains", "user_location",
"search_context_size")`, and helpers `canonical_for` / `wire_type_for` /
`is_builtin_name`. Canonical name: `web_search` (D1).

Unknown builtins (`code_execution_20250522`, `file_search`, `computer`) decode
to a builtin `ir.Tool` with the raw wire type kept in
`builtin_config["_wire_type"]`; providers that can't host it drop it with a
`log.warning` — never mangled into a function tool (the old bug), never
hard-error.

### 8.2 Wire decode: Anthropic + Responses

**Files**: `wiwi/wire/anthropic_messages.py`, `wiwi/wire/openai_responses.py`

Anthropic tools loop: no `type`/`"custom"` → function tool (unchanged);
`canonical_for("anthropic", type)` → `ir.Tool(builtin="web_search", …)` with
the config subset; unknown type → the D5 path above. `*_tool_result` decode
keeps `block_type`. `server_tool_use` history decode is unchanged (plain
`ToolUsePart`; the name carries builtin-ness).

Responses tools filter: `"function"` unchanged; `web_search` /
`web_search_preview` / `web_search_2025_08_26` → canonical `web_search` with
`builtin_config` from `search_context_size`, `user_location`,
`filters.allowed_domains`, `filters.blocked_domains`; other types → D5. Per
A2, `web_search_call` items in the Responses *input* array stay skipped:
decoding them would replay as an unpaired `server_tool_use` on an Anthropic
upstream (the A1 trap, inbound side). Codex history stays valid — text turns
remain.

### 8.3 Provider encode: four surfaces, four shapes

**Files**: `wiwi/providers/anthropic_adapter.py`, `gemini_adapter.py`,
`openai_adapter.py`, `openrouter_adapter.py`

- **Anthropic**: builtins render `{"type": "web_search_20250305", "name":
  "web_search", **config}` (only `max_uses`, `allowed_domains`,
  `blocked_domains`, `user_location`, `cache_control`; `search_context_size`
  dropped — no clean map). History: `is_builtin_name(name)` re-emits
  `server_tool_use`; `ToolResultPart` emits its `block_type`, so a real
  `server_tool_use` + `web_search_tool_result` pair replays paired, in order.
- **Gemini**: builtin `web_search` → sibling `{"google_search": {}}` entry in
  the same `tools` list (config dropped; Gemini takes `{}`). Grounding decode
  synthesizes **nothing** — a synthesized `ToolUsePart` would set
  `stop_reason="tool_call"` and invite clients to return a result the model
  never requested.
- **OpenAI Chat** (base `_encode_tools`): drops builtins with
  `log.warning("dropping_unhostable_builtin_tool")` — Chat Completions has no
  hosted search tool. The tools block was refactored into
  `_encode_tools(req) -> list | None` (used by NIM/BAI/WorkBuddy/Cline via
  subclassing; behavior unchanged for them). `web_search_options` extras
  passthrough still forwards for always-search models.
- **OpenRouter**: overrides `_encode_tools` — the one adapter that *hosts*
  rather than drops: `{"type": "openrouter:web_search", "parameters": {…}}`
  with `blocked_domains → excluded_domains`.

The drop is capability-driven, independent of `drop_params`.

### 8.4 Streaming: delta flag + per-surface suppression (A1)

**Files**: `wiwi/streaming/deltas.py`, `wire/anthropic_messages.py`,
`wire/openai_chat.py`, `wire/openai_responses.py`,
`providers/anthropic_adapter.py`

`dl.ToolCallOpen.builtin: str | None = None` — encoders use it to suppress or
re-render as a hosted item. The Anthropic stream decoder tags **any**
`server_tool_use` block start (`builtin = name or "server_tool"`): any such
block is provider-executed by definition, registry-known or not — an unmapped
one (e.g. `code_execution`) must not leak as a phantom function call.

While citations are deferred, our responses must not emit half a search trace:

- **Anthropic surface**: builtin tool calls suppressed entirely (text only).
  `server_tool_use` requires a paired `web_search_tool_result` when history
  replays; emitting the call without the result risks a 400 on turn 2 and
  gains nothing.
- **Chat surface**: same suppression — a function `tool_calls` frame for
  `web_search` invites the client to execute a phantom function.
- **Responses surface**: **emits** self-contained `web_search_call` output
  items (single `output_item.added` at open, single `output_item.done` at
  close; no `function_call_arguments` events; query from the call's args).
- **Guard**: `stop_reason == "tool_call"` with every call suppressed
  downgrades to `stop` (Chat) / `end_turn` (Anthropic) — an Anthropic response
  with `stop_reason: tool_use` and no `tool_use` block is spec-invalid.

Suppressing a `ToolCallOpen` turns the paired `ArgsDelta`/`Close` into
orphans, which both encoders already drop (round-25 defenses), so no
args-only phantom frames leak.

### 8.5 Gateway + logging

`core/gateway.py` `_tool_schemas` excludes `t.builtin is not None` — advisory
validation never flags a provider-correct builtin call (IR-level flag, not a
dialect/provider branch). `server/app.py` `_capture_delta` records
`"builtin": d.builtin` in the tools_map log entry.

### 8.6 Known losses (v1, deliberate)

- `max_uses` ↔ `search_context_size` has no clean map — dropped on the
  surface that lacks it, with warning.
- Version-specific Anthropic features (20260209 dynamic filtering beyond
  domains, 20260318 response inclusion) collapse to the 20250305 common
  subset on encode.
- Response-side search trace invisible to Anthropic/Chat clients (A1) — the
  direct consequence of deferring citations. Round 2 adds proper result
  carrying (`AssistantTurn` extension + delta contract change) and the full
  trace.
- Anthropic bills `web_search_requests` separately (`usage.server_tool_use`) —
  not modeled; pricing may undercount search costs. Flagged for
  `cost/pricing.py` future work.
- **Phase 2 (separate change)**: OpenAI-outbound hosting requires a Responses
  pivot — hosted `web_search` is Responses-API-only on OpenAI, and
  `_build_url` runs *before* `encode_request` at all three gateway call
  sites, so the adapter can't pick `/v1/responses` after seeing the body.

**Files changed** (this round): `wiwi/ir/types.py`,
`wiwi/ir/builtin_tools.py` (new), `wiwi/wire/anthropic_messages.py`,
`wiwi/wire/openai_responses.py`, `wiwi/wire/openai_chat.py`,
`wiwi/providers/anthropic_adapter.py`, `wiwi/providers/gemini_adapter.py`,
`wiwi/providers/openai_adapter.py`, `wiwi/providers/openrouter_adapter.py`,
`wiwi/streaming/deltas.py`, `wiwi/core/gateway.py`, `wiwi/server/app.py`.

**Tests**: `tests/test_web_search_translation.py` (new, 51 tests) +
`tests/test_property_roundtrip.py` (3 new properties).

---

# Round 39 — thinking/tool-call translation hardening (2026-09-09)

> **Status:** all changes verified — 1457 tests pass, ruff clean
> **Scope:** the thinking / tool-call / translation-helper chain. Every fix
> was reproduced by direct execution before patching; regressions live in
> `tests/test_fix_round38.py` (33) + `tests/test_streaming_improvements.py` (4).
> AUDIT entries #63/#64/#65 marked fixed in place.

## 39.1 Crash class: null/junk client values must not 500 the gateway

`run_chat_like` catches only `(DialectError, ValueError)`; every `TypeError`
/`AttributeError` below surfaced as a 500 with an `internal gateway error`
body. All reproduced live pre-fix:

- **`thinking: null` history blocks** (Anthropic streams them on
  redacted-thinking turns) produced `ThinkingPart(text=None)`; the OpenAI
  adapter's `reasoning += p.text` raised TypeError, the Anthropic adapter
  re-emitted `{"thinking": null}`. Coerced to `""` at decode
  (`wire/anthropic_messages.py`).
- **`text: null` blocks** → `TextPart(text=None)`, same crash class. Coerced.
- **Non-string `thinking.budget_tokens` / `max_tokens`** (e.g. `"1024"`)
  flowed into `GenParams` raw; the Anthropic adapter's `<=`/`>` comparisons
  raised TypeError. Numeric strings / whole floats coerced; garbage → None.
- **Non-dict entries** in `messages`/`system` lists (string, number) crashed
  `.get` in both wire codecs — now skipped, per the policy every sibling
  loop already states.
- **Tool-call `arguments` as a JSON object** (some OpenAI-compatible gateways)
  raised `TypeError: the JSON object must be str...` in BOTH wire codecs AND
  both OpenAI adapter decode paths — replayed history 500'd every surface.
  Object args are now used directly (and re-serialized for `raw_args`);
  the stream path serializes dict fragments to str before
  `ToolCallArgsDelta` (a dict fragment previously crashed the Responses
  encoder's `+=`).
- **Chat codec junk shapes**: non-dict `tools[]` entries / `function`
  sub-objects / `tool_choice.function` / `stream_options` — all now skipped
  or ignored instead of AttributeError.
- **Gemini null `text` parts** crashed `turn.text +=` / `TextDelta(None)`.
- **String `stop_sequences`** (`"END"`) was iterated per character → three
  single-char stops. Now one sequence.

## 39.2 `redacted_thinking` blocks round-trip (Anthropic)

`ThinkingPart` gains `block_type` + `data` (mirrors
`ToolResultPart.block_type`). The wire codec decodes
`{"type": "redacted_thinking", "data": …}` blocks (previously silently
dropped — an empty replayed assistant turn, and a hard 400 on the next
thinking-enabled turn since Anthropic requires the block back). The adapter
re-emits `{"type": "redacted_thinking", "data": …}` verbatim on encode and
decodes it in `decode_response`. Other adapters see empty text and skip it.

## 39.3 Unknown reasoning effort must not silently enable thinking

`effort_to_thinking_budget` mapped unknown strings to **medium (8000)** —
the docstring said None, the code disagreed. A typo (`"hight"`) switched
thinking ON with a large budget on Anthropic/Gemini, and was forwarded
verbatim to OpenAI upstreams (instant 400). Now: unknown → `None`; the
Anthropic adapter leaves thinking OFF when no budget resolves (no more
medium fallback); the OpenAI adapter only forwards known effort levels;
Gemini inherits the None via `effective_thinking_budget`.

## 39.4 `thinking_budget=0` disables reasoning on OpenRouter

The documented thinking-off value (honored by Anthropic/Gemini/OpenAI since
round 31) was clamped to `max(g.thinking_budget, 1024)` — thinking ON at the
minimum for an explicit disable. Now `{"enabled": False}`, matching
`reasoning_effort: "none"`.

## 39.5 Responses stream-encoder integrity (AUDIT #63 + #65)

- **#65**: `TextDelta`/`ThinkingDelta` while `_item_open == "tool"` no
  longer call `_close_item()` (which popped the tool, dropped its later args
  fragments, and emitted a mid-stream `output_item.done`). The interleave is
  suppressed — the same policy the Anthropic encoder has pinned since
  round 25 — and the tool's args keep streaming on their own output_index.
- **#63**: `ToolCallArgsDelta` for a builtin-tagged open accumulates into
  the buffer (close-time `_builtin_query` needs it) but emits **no**
  `function_call_arguments.delta` frame — the frame's `fc_<req>_<n>`
  item_id never had an `output_item.added`, and Codex CLI accumulated
  fragments against a nonexistent function item.

## 39.6 `_repair_truncated_json` odd backslash runs (AUDIT #64)

The endswith heuristic handled only a single trailing backslash; an odd run
≥ 3 (escaped pair + dangling escape) produced invalid JSON → the whole
args object silently fell to `{}`. Now: count the trailing run, strip the
final backslash when odd; the `\uXXXX` strip is escape-aware (fires only
when the backslash run before `u` is odd, so `"C:\\u0f"` — complete pair +
literal `u0f` — is untouched, while pair + fresh `\u0f` strips only the
fresh tail). Pinned with 3/5-run, pair+partial-escape, and
complete-escape cases.

**Files changed (this round):** `wiwi/ir/types.py`,
`wiwi/wire/anthropic_messages.py`, `wiwi/wire/openai_chat.py`,
`wiwi/wire/openai_responses.py`, `wiwi/providers/anthropic_adapter.py`,
`wiwi/providers/openai_adapter.py`, `wiwi/providers/openrouter_adapter.py`,
`wiwi/providers/gemini_adapter.py`, `wiwi/streaming/partial_json.py`,
`AUDIT.md` (#63/#64/#65 marked fixed).

**Verified live** (ASGITransport + respx, real `run_chat_like` pipeline):
null-thinking history, object-args history, and junk message entries all
return 200 with the upstream receiving correctly-encoded bodies (pre-fix:
500 `internal gateway error`).

---

# Round 40 — journal replay integrity: #66, #67, #68 (2026-09-10)

> **Status:** all changes verified — 1469 tests pass, ruff clean
> **Scope:** the durable stream-replay layer (AUDIT #66/#67/#68, the last
> live items from the 2026-09-09 streaming audit). Every other register
> item from the audit's priority list (#52, #54–#62, #5–#13, #15, #24–#26,
> #31–#39, #41–#50) was re-verified against current source as ALREADY FIXED
> — earlier sessions fixed them without marking the register; do not re-fix.
> `tests/test_fix_round39.py` (12) pins this round.

## 40.1 #66 — reconnect to an active empty journal no longer re-dispatches

`JournalStore.is_active(request_id)` exposes same-process liveness, and the
replay gate in `run_chat_like` is now `replay or complete or ACTIVE`. A
sub-second reconnect (the TTFT window where the journal file exists but has
no chunks) now tails the same journal until the original stream finishes —
previously it fell through to a fresh upstream dispatch, double-billing the
logical request. The eager file-touch in `open()` always intended this; the
gate's shape defeated it.

## 40.2 #67 — journal replay is scoped to the originating key

`JournalStore.open(request_id, key_id=...)` writes an internal ownership
record (`{"seq": 0, "owner": "<key_id>"}`) as the journal's first line:
invisible to `read_after` (which filters `seq > last_seq` with data records
starting at seq 1) and `is_complete` (which checks only `done`). `owner_of()`
reads it back and survives process restarts (it is in the file, not memory).
The replay branch compares the journal's owner against the caller's
`key_id`; a mismatched key gets no replay — it falls through to its own
dispatch and never sees the other key's streamed content. Pre-scoping
journals (no owner record) remain readable for back-compat.

## 40.3 #68 — evicted tape head refuses resume

`StreamTape.head_evicted(last_seq)` is true when the first surviving entry
is not `last_seq + 1` — the continuation's head was evicted.
`gateway._attempt_resume` checks it (with `tape.seq - 1`, the last delta
the consumer emitted) and refuses the resume, so the caller falls back to a
fresh attempt instead of silently building a partial continuation (the
evicted-tool-Open case: `replay_tool_calls` returned `[]` while Args/Close
survived, and the model re-invoked a tool the client already saw).

**Files changed:** `wiwi/streaming/tape_store.py` (`is_active`, key-scoped
`open`, `owner_of`, owner-aware `_read_records`), `wiwi/server/app.py`
(replay gate + key scoping + `key_id` into `open()`), `wiwi/streaming/resume.py`
(`head_evicted`), `wiwi/core/gateway.py` (resume guard), `AUDIT.md`
(#66/#67/#68 marked fixed).

**Verified live:** e2e reconnect-while-empty with a counting stub upstream
(exactly ONE upstream call total); cross-key replay blocked with two minted
virtual keys against a real app instance while same-key replay still works;
ownership readable after a simulated restart.

## 40.4 Runtime journal TTL sweep (AUDIT coverage gap)

Journals were swept only at startup — the lifespan comment in
`server/app.py` even said "the sweep is not run again while serving" — while
`tape_store.py`'s docstring promised a periodic background timer. A
long-lived gateway accumulated expired journals for the whole process
lifetime; the TTL was never enforced after startup.

`JournalStore.sweep_forever(interval_s)` is the sweeper body (sleep, sweep
via `asyncio.to_thread`, log removals; exceptions are caught and logged —
the sweeper must never die). `start()`/`stop()` follow the same
worker-lifecycle convention as ClineAutoRefresh/HealthHealer: `start` is
idempotent (reuses the live task), `stop` cancels and awaits, also
idempotent. The lifespan starts it when `stream_journal_enabled` with
interval `min(60, max(1, ttl/4))` s and stops it at shutdown, so expiry lags
the configured TTL by at most one interval.

**Files changed:** `wiwi/streaming/tape_store.py` (`sweep_forever`/`start`/
`stop`), `wiwi/server/app.py` (lifespan start/stop + corrected comment).

**Tests:** `tests/test_fix_round40.py` — sweeper removes an expired journal
and keeps a fresh one; start/stop lifecycle (no double task, idempotent
stop, no sweep after stop); lifespan integration (started when journaling
on, absent when off).

## 41. WorkBuddy default reasoning_effort = "max"

**File**: `wiwi/providers/workbuddy_adapter.py` (`encode_request`)

**Issue**: The WorkBuddy adapter sets `provider_type="openai-compatible"` on
its params so the base OpenAI encoder strips per-message `reasoning_content`
(quirk 4). That same flag made the base encoder skip `reasoning_effort`
entirely, so a client that sent no reasoning preference got the upstream's
implicit default and no explicit effort — and a client that *did* send
`reasoning_effort` (or an Anthropic `thinking_budget`) had it silently
dropped too.

**Before**:
```python
params["provider_type"] = "openai-compatible"
body = super().encode_request(req, model_id, params)
# reasoning_effort never applied
```

**After**:
```python
g = req.gen_params
explicit = g.effective_reasoning_effort()   # reasoning_effort or mapped budget
if explicit:
    body["reasoning_effort"] = explicit     # caller wins
else:
    body["reasoning_effort"] = _DEFAULT_REASONING_EFFORT  # "max"
```

`effective_reasoning_effort()` normalizes both dialect shapes: a Chat/Responses
`reasoning_effort` passes through unchanged, an Anthropic `thinking_budget` is
mapped through the shared effort table, and an explicit `"none"` stays `"none"`
(disables thinking) instead of being replaced by the default.

**Tests:** `tests/test_workbuddy_adapter.py` —
`test_encode_defaults_reasoning_effort_to_max`,
`test_encode_passes_through_caller_reasoning_effort`,
`test_encode_maps_thinking_budget_to_effort`,
`test_encode_reasoning_effort_none_disables`. All fail against the pre-fix
encoder.

---

# Round 46 — HealthHealer SSE probe classification: #119 (2026-09-12)

> **Status:** verified — regression + control green against the fix, primary
> RED against the pre-fix source (module stashed), full pytest + ruff clean.
> **Scope:** the HealthHealer probe body classifier. AUDIT #96's fix only
> `json.loads`-ed a bare 200 body; a force_stream probe's answer is SSE, so a
> WorkBuddy dead-session envelope inside a `data:` frame was classified
> HEALTHY and the still-dead key was restored into probation. Pinned by
> `tests/test_fix_round46.py`.

## 46.1 #119 — SSE-wrapped error envelopes are unhealthy

**File**: `wiwi/core/recovery.py` (`_body_is_error_envelope`)

**Issue**: `_probe` sends force_stream probes (WorkBuddy, Cline) with
`stream=True`, so the upstream answers with an SSE body — including business
errors on HTTP 200, where the envelope rides inside a `data:` frame (e.g.
`data: {"code": 12153, "msg": "Offline user session"}`). The #96 fix tested
only the bare-JSON body shape, so `json.loads(b'data: {...}\n\n')` raised,
`_body_is_error_envelope` returned False, the probe verdict was HEALTHY, and
with `probes_to_restore=1` the still-dead key was restored into probation —
re-exposing a dead credential to live traffic.

**After**:
```python
data = json.loads(body)              # bare 200 body (original #96 path)
if is_error(data): return True
parser = LineSSEParser()             # force_stream answers arrive as SSE
for line in text.splitlines():
    event = parser.feed_line(line)
    if event is not None and is_error_payload(event.data):
        return True
event = parser.flush()               # final frame without trailing blank line
return event is not None and is_error_payload(event.data)
```

The same `{"code": N≠0}` test (with the bool/int guard) is applied to each
parsed SSE frame's payload; multiline `data:` payloads are joined by the
parser; a final frame without a trailing blank line is covered by `flush()`
(the DeepSeek/B.A.I trailing-`[DONE]` case). `{"code": 0}` success envelopes
and healthy SSE completions remain HEALTHY — pinned by the control test.

**Files changed (this round):** `wiwi/core/recovery.py` (`_body_is_error_envelope`
+ `LineSSEParser` import), `tests/test_fix_round46.py`, `AUDIT.md`
(#119 moved from the round-45 open register to ✅ Fixed — round 46).

**Verified live** (respx fake upstream through the real `HealthHealer._sweep`
and WorkBuddy adapter): the SSE error envelope leaves the terminally-retired
key `invalid` (pre-fix: restored to `probation`), while the healthy SSE body
still restores it to `probation`.

---

## 47.1 Terminal frames must close *every* open tool block (AUDIT #111)

**Affects:** `wiwi/wire/anthropic_messages.py` (`final_frame`),
`wiwi/wire/openai_responses.py` (`_completed`) — the Anthropic and Responses
surfaces, i.e. Claude Code and Codex CLI.

Parallel tool calls are **siblings, not sequential**: both encoders already
avoid closing an open tool item when a new `ToolCallOpen` arrives (that is the
round-7 fix). But the *terminal* frame closed only the single currently-open
block, so any adapter that ends the message with more than one tool call still
open left the others unfinished forever.

**Before** (`final_frame`, after the single current-block close):

```python
out = b"".join(self._close_block())
```

**After:**

```python
out = b"".join(self._close_block())
# Parallel tool calls are siblings: an adapter may end the message with
# several tool_use blocks still open (the Anthropic upstream omits their
# content_block_stop). _close_block() only closes the *current* one, so
# every remaining registered index needs its own stop or the client is
# left with a tool_use block that never finishes (AUDIT #111).
for idx in sorted(self._tool_blocks):
    out += b"".join(self._close_block(tool_index=idx))
```

`_completed` gets the mirror change (`for idx in sorted(self._tools):
closing += b"".join(self._close_tool(idx))`). Note `_close_tool` **pops** its
entry from `self._tools`, so the sweep is also what makes the terminal
`response.completed` payload carry the missing `function_call` items at all.

**Idempotence:** the no-arg `_close_block()` runs first and clears
`_open_tool`; `_close_block(tool_index=idx)` returns `[]` for an index already
popped (`anthropic_messages.py:371-373`). Verified against the worst case —
index 0 opened *last*, so it is simultaneously the currently-open block and a
sweep target — giving exactly one stop per index and no `KeyError`.

**Live trigger (why this is not theoretical):** the OpenAI adapter's `[DONE]`
early return (`openai_adapter.py:364-365`) does not flush open tool state, so
every `[DONE]`-terminated stream with tool calls reaches the encoder with
tools still open. Two parallel calls + `[DONE]` gave
`content_block_start ×2 / content_block_stop ×1` pre-fix. (The stop *reason* on
that same path is still wrong — that is AUDIT #133, filed open.)

**Files changed:** `wiwi/wire/anthropic_messages.py`,
`wiwi/wire/openai_responses.py`, `tests/test_fix_round47.py`, `AUDIT.md`
(#111 marked fixed in place).

## 47.2 The OpenAI adapter's synthesized-open markers must not survive `finish_reason` (AUDIT #129)

**Affects:** `wiwi/providers/openai_adapter.py` — the "adopt the real id" branch
(`:407-420`), which is shared with every adapter that inherits
`decode_stream_event` from `OpenAIAdapter` (`cline`, `bai`, `workbuddy`).

When a provider sends `arguments` with no `id`, the adapter synthesizes
`ToolCallOpen(index, id="", name="")` and records the index in
`_synthesized_opens`. If the real id arrives later, the branch at `:407` adopts
it and `continue`s past the open-emitting code — correct, because an Open was
already emitted.

The `finish_reason` sweep cleared `_open_tool_indices`, `_tool_names`, and
`_pending_opens` — but **not** `_synthesized_opens`. A later tool call reusing
that index then took the adopt branch and emitted `ToolCallArgsDelta` with no
preceding `ToolCallOpen`; the encoders drop args for an unregistered index
(`openai_chat.py:319-322`), so the call vanished silently.

**Fix:** clear `_synthesized_opens` (and the write-only `_emitted_opens`) in the
same sweep. `reset()` already cleared both; only the per-finish path missed them.

**Files changed:** `wiwi/providers/openai_adapter.py`,
`tests/test_fix_round47.py`
(`test_openai_adapter_reopen_after_finish_emits_open_before_args`), `AUDIT.md`
(#129).

**Scope note:** `NimAdapter` overrides `decode_stream_event` and carries the
adoption half only (AUDIT #88, fixed in round 41) — it does not share this
defect. `OpenRouterAdapter` also overrides it and lacks the *synthesize* branch
entirely, which is a separate open finding (AUDIT #135).

### Known-open follow-ups from this round — **all fixed in round 49**

Both follow-ups below were fixed on 2026-09-13; see §49.1 and §49.2. The
entries are kept here because the *diagnosis* is what a later agent needs, and
`AUDIT.md` now carries the fix record.

- **#133** `[DONE]`-terminated streams: tools left open **and** `stop_reason`
  synthesized as `stop`/`end_turn` while tool calls were delivered. Same
  `[DONE]` early return as 47.1's trigger; the fix is to flush open tool state
  in that arm exactly as the `finish_reason` arm does. Also present at
  `openrouter_adapter.py:228` and `opencode_adapter.py:208`.
- **#135** OpenRouter has no `elif idx not in self._open_tool_indices:`
  synthesize branch, so a tool chunk carrying args but no `id` emits a bare
  `ToolCallArgsDelta` — the call disappears from every dialect.

## 49.1 `[DONE]` must flush open tool state *and* derive the stop reason (AUDIT #133)

**Affects:** `wiwi/providers/openai_adapter.py` (the `[DONE]` arm, and every
adapter that inherits or mirrors it), `wiwi/providers/openrouter_adapter.py`.
Reaches the Chat, Anthropic, and Responses surfaces through whichever encoder
the caller used.

Round 47 fixed the *encoder* side of this (§47.1: the terminal frame now closes
every open block), which masked the client-visible half of the defect. What
remained is that a `[DONE]`-terminated stream never produced a `Finish` at all,
so the gateway's `finish is None` branch (`gateway.py:1022-1040`) synthesized
`Finish("stop")` for a turn that had delivered tool calls. Claude Code reads
`stop_reason: "end_turn"` and concludes the turn ended without tool use — it
stops the agent loop. The tools were delivered and never acted on.

**Before:**

```python
def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
    if data == "[DONE]":
        return [dl.StreamEnd()]
```

**After** — a shared helper on the base adapter, so the OpenRouter subclass
that overrides `decode_stream_event` gets the same behaviour:

```python
def _flush_open_tools(self) -> list[dl.IRStreamDelta]:
    out: list[dl.IRStreamDelta] = []
    for open_idx in sorted(self._open_tool_indices):
        if open_idx in self._pending_opens:
            cid, cname = self._pending_opens.pop(open_idx)
            out.append(dl.ToolCallOpen(index=open_idx, id=cid, name=cname))
        out.append(dl.ToolCallClose(index=open_idx))
    self._open_tool_indices.clear()
    self._tool_names.clear()
    self._pending_opens.clear()
    self._synthesized_opens.clear()
    return out

def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
    if data == "[DONE]":
        out = self._flush_open_tools()
        if out:
            out.append(dl.Finish("tool_call"))
        out.append(dl.StreamEnd())
        return out
```

**Why `Finish("tool_call")` and not the bare `StreamEnd` the round-47 sketch
proposed.** The sketch said to flush and terminate, letting the gateway derive
the stop reason. It cannot: the gateway's synthesis branch fires precisely when
`finish is None`, and its fallback is `"stop"` — the wrong answer, and the one
this finding is about. The flush must carry the content-derived reason with it.

**The three-way branch this preserves** (each pinned by a control in
`tests/test_fix_round49.py`):

| Stream shape | Emitted tail | Why |
|---|---|---|
| text, then `[DONE]` | `[StreamEnd()]` | `_flush_open_tools()` returns `[]`, so the gateway's round-15 synthesis still owns this path unchanged |
| tools, then `[DONE]` | `Close`s + `Finish("tool_call")` + `StreamEnd` | the fix |
| tools, `finish_reason`, then `[DONE]` | `[StreamEnd()]` | the finish sweep at `openai_adapter.py:501-514` already cleared the state, so the flush is empty — no duplicate `Finish`, no orphan `Close` |

**OpenCode needs no change.** Its `[DONE]` arm delegates to `self._sub()` for
the chat and messages routes (`opencode_adapter.py:203-209`), so the inherited
fix applies; the responses route has its own `_resp_ended` guard and never had
the defect.

**Files changed:** `wiwi/providers/openai_adapter.py`,
`wiwi/providers/openrouter_adapter.py`,
`tests/test_fix_round49.py` (6 tests), `AUDIT.md` (#133).

## 49.2 OpenRouter must synthesize *and* adopt tool opens like the base adapter (AUDIT #135)

**Affects:** `wiwi/providers/openrouter_adapter.py` — the OpenRouter provider
type on every surface.

`OpenRouterAdapter` overrides `decode_stream_event` wholesale, and its copy had
drifted from `OpenAIAdapter`'s in two places. The missing *synthesize* branch
was the filed finding; the missing *adopt* branch was latent behind it.

**Before** (the args arm — no `elif`):

```python
if fn.get("arguments"):
    if idx in self._pending_opens:
        cid, cname = self._pending_opens.pop(idx)
        out.append(dl.ToolCallOpen(index=idx, id=cid, name=cname))
    out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=fn["arguments"]))
```

**After:**

```python
if fn.get("arguments"):
    if idx in self._pending_opens:
        cid, cname = self._pending_opens.pop(idx)
        out.append(dl.ToolCallOpen(index=idx, id=cid, name=cname))
    elif idx not in self._open_tool_indices:
        self._open_tool_indices.add(idx)
        self._tool_names[idx] = name_fragment or ""
        self._synthesized_opens.add(idx)
        out.append(dl.ToolCallOpen(index=idx, id="",
                                   name=self._tool_names[idx]))
    out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=fn["arguments"]))
```

Adding the synthesize branch **requires** the matching adopt branch in the
id-first arm, or the synthesized open becomes a second `ToolCallOpen` for the
same index when the real id finally arrives:

```python
if tc.get("id"):
    if idx in self._synthesized_opens:
        self._synthesized_opens.discard(idx)
        self._tool_names[idx] = name_fragment or ""
        if fn.get("arguments"):
            out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=fn["arguments"]))
        continue
    if idx in self._open_tool_indices:
        ...
```

**The synthesized Open must carry the name.** The first cut of this fix emitted
`ToolCallOpen(index=idx, id="", name="")` — the branch *stores* `name_fragment`
in `_tool_names[idx]` on the line above, and then dropped it. Unit tests passed
(the Open existed, was correctly nested, and the args reassembled), but
`AnthropicStreamEncoder` renders the Open as a `tool_use` block, and a block
with `name: ""` cannot be dispatched by the client: the call arrives and the
agent loop has nothing to invoke. Caught by the real-TCP harness, not the unit
tests. Emit `name=self._tool_names[idx]`.

The lesson generalizes past this fix, but **not** as "assert on the encoded
output" — the e2e file already did that and still passed against the broken
code. The existing `tools: tool_use block opens with the upstream tool name`
check decodes the Anthropic SSE and asserts the rendered `tool_use` block's
name; it stayed green because its fixture (`fake-tools`) sends a real `id` on
its first chunk, so it takes the *id-first* arm and never reaches the
synthesize branch. Verified by reverting the fix: the `fake-tools` assertion
passes, the `fake-argsnoid` one fails.

So the discriminating question is not how strong the assertion is but **which
branch the fixture drives**. Assertion strength and branch coverage are
independent axes; only the second one failed here. `fake-tools` covers id-first
and `fake-argsnoid` covers synthesize, which completes the pair — but a third
tools fixture must choose its arm deliberately rather than inheriting "the
tools path is covered" from the one that exists.

**The same bug existed a third time, at `nim_adapter.py:332`** (AUDIT #143,
fixed round 51). This entry originally said "in *both* adapters" — but the
three synthesize branches are copy-derived, and "both" described which files
had been opened, not which files had the defect. The cheap check that finds
all of them at once is a grep for the *shape* of the just-fixed bug
(`grep -n 'name=""' wiwi/providers/*.py`), which costs nothing and does not
depend on a fixture reaching the arm. Run it over the
`synthesize` / `adopt` / `_synthesized_opens` families whenever one changes.

`_synthesized_opens` already exists on `OpenRouterAdapter` — it defines no
`__init__` and inherits `OpenAIAdapter`'s — so no state plumbing was needed.
The invariant to preserve: **one `ToolCallOpen` per index per call**, whether
the id arrives first, last, or never. The regression test
`test_openrouter_args_without_id_matches_openai_adapter` drives both adapters
with one frame list and asserts identical delta-kind sequences — the divergence
between the two was the bug, so pinning them equal is the durable check.

**Files changed:** `wiwi/providers/openrouter_adapter.py`,
`tests/test_fix_round49.py` (4 tests), `AUDIT.md` (#135).

---

## 54. WorkBuddy forwards the caller's reasoning effort verbatim, uncapped

**File**: `wiwi/providers/workbuddy_adapter.py` (`encode_request`)

**Invariant**: whatever reasoning level the caller's agent client expresses
reaches the WorkBuddy upstream as the same `reasoning_effort` value — no
cap, no whitelist, no silent re-enable:

- `reasoning_effort` (OpenAI Chat) and `reasoning.effort` (Responses /
  Codex CLI) pass through **verbatim**, including levels the IR's own effort
  table does not know — WorkBuddy owns validation of its field.
- An Anthropic `thinking.budget_tokens` (Claude Code) is **translated** to the
  nearest effort level by `wiwi.ir.types.thinking_budget_to_effort` — that is
  a translation, not a clamp. A huge budget (e.g. 999999) maps to `"xhigh"`;
  it is never capped down or rejected.
- An explicit disable (`"none"` effort, budget 0, `thinking: disabled`)
  becomes `"none"` upstream — never replaced by the default.
- Only a caller that expressed **no** preference gets
  `_DEFAULT_REASONING_EFFORT` (`"max"`), per entry 41. Anthropic
  `thinking: {"type": "adaptive"}` carries no level of its own, so it also
  gets the default.

The stale comment claiming "a bare Chat request never carries
reasoning_effort" was corrected — Chat requests do carry it and it is
respected; no code change was needed.

**Tests:** `tests/test_fix_round53.py` — end-to-end (real agent payloads
through the wire codecs into `WorkBuddyAdapter.encode_request`):
`test_responses_effort_levels_pass_through_verbatim`,
`test_chat_effort_levels_pass_through_verbatim`,
`test_chat_unknown_effort_not_filtered`,
`test_anthropic_budget_maps_to_nearest_level`,
`test_anthropic_budget_is_never_capped`,
`test_anthropic_disabled_and_zero_stay_disabled`,
`test_anthropic_adaptive_defaults_to_max`,
`test_no_client_preference_defaults_to_max`.

---

## 55. Scalar tool-args fragments corrupt the stream; sync Anthropic encode drops redacted_thinking

Two AUDIT entries (#124, #125), fixed together in round 54 because both are
"a value reached a typed slot unchecked" defects on the translation boundary.

### #124 — `arguments` as a truthy scalar

**Files**: `wiwi/wire/openai_chat.py` (`decode_request`),
`wiwi/providers/base.py` (`coerce_args_fragment`),
`wiwi/providers/openai_adapter.py`, `wiwi/providers/openrouter_adapter.py`,
`wiwi/providers/nim_adapter.py`

`json.loads` raises `TypeError` — not `JSONDecodeError` — for a non-string,
so `arguments: true` (or `5`, `1.5`, `["a"]`) escaped the Chat codec's
`except` clause and 500'd the request. The Responses codec already defended
this (`_load_args`); the Chat codec was the only surface that did not.

The same shape in the provider decoders was worse on the streaming path than
the register assumed. `ToolCallArgsDelta.args_fragment` is typed `str`, the
scalar was placed on the delta unchecked, and the client encoder's frame
serialization raised **after** the response had started:

```
HTTP 200
data: {…"tool_calls":[{"index":0,…,"function":{"name":"f","arguments":""}}]…}
data: {…"tool_calls":[{"index":0,"function":{"arguments":true}}]…}
data: {"error":{"message":"sequence item 0: expected str instance, bool found",…}}
```

A valid upstream response became a corrupt stream. Fixed at the boundary with
one shared helper, `coerce_args_fragment` (str passes through, dict is
re-serialized, anything else becomes `""`), applied at every live site —
including NIM's aliased-tool buffer, where `str + bool` raised on concatenation.

### #125 — sync `/v1/messages` dropped `redacted_thinking`

**File**: `wiwi/wire/anthropic_messages.py` (`encode_response`)

The streaming encoder has emitted `redacted_thinking` verbatim since #103 and
`AnthropicAdapter.encode_request` replays it, but the client-facing **sync**
encode had no redacted branch: an opaque blob rendered as
`{"type": "thinking", "thinking": ""}` — data dropped, block unsigned, so the
client's next-turn history was 400-bait. The mirror branch now emits
`{"type": "redacted_thinking", "data": t.data}`.

**Tests:** `tests/test_fix_round54.py` (15) — codec-level guards for every
scalar shape, controls for the string/dict/ordinary-thinking paths, provider
decoder coverage for the three adapters, and two end-to-end app tests (the
replayed-scalar 500 and the corrupt-stream case) driven through `create_app`.

---

## 56. OpenCode's version refresh reads npm, not a rate-limited GitHub API (AUDIT #147)

**File**: `wiwi/providers/opencode_version.py`

`refresh_version()` read the live opencode version from GitHub's
`releases/latest` REST API. Anonymous calls to that API are capped at 60
requests/hour **per IP** and the 5-minute sweep alone spends 12 of them, so
on a shared egress IP the budget runs out and the API answers a bare `403`:

```
2026-09-15 19:40:48 [warning  ] opencode_version_fetch_bad_status status=403
```

The cache then never fills and `OpencodeAdapter.headers()` sends
`User-Agent: opencode/unknown` — the stale fingerprint the live refresh
exists to prevent. The request still succeeds (Zen's client gate is
`x-opencode-session`, not the UA version), so the degradation was silent.

The sweep now reads the npm registry — the CLI's distribution source of
truth, the endpoint opencode's own `Installation.latest` uses for npm/bun/pnpm
installs, and the source the Cline/WorkBuddy helpers already read:

```
GET https://registry.npmjs.org/opencode-ai/latest
-> {"version": "x.y.z"}
```

`_parse_tag` (GitHub's leading-`v` stripper) became `_parse_version`, which
sanitizes the registry value for a header value — CR/LF/NUL stripped,
256-char cap, non-string rejected — since the response is remote input that
lands in a `User-Agent`. Nothing else changed: same 5-minute TTL, same
background sweep, same stale-while-revalidate read from the synchronous
`headers()` path.

**Tests:** `tests/test_fix_round58.py` (3) — the refresh asserts the GitHub
API is *not* called (the regression guard), registry failure keeps the stale
version, and the header sanitizer covers CRLF, padding, non-string, and
empty input. `tests/test_fix_round27.py`'s two refresh tests were migrated to
`NPM_LATEST_URL`; its `test_parse_tag_strips_v` was deleted along with the
helper.

**Live:** `refresh_version()` → `1.18.31` (matches GitHub's `v1.18.31`), and
a real `big-pickle` request through the adapter's headers returns HTTP 200.


## NIM: strip OpenAI-2026 platform params (`prompt_cache_key` 400) — 2026-09-16

`NimAdapter.encode_request` (`wiwi/providers/nim_adapter.py`) now strips the
OpenAI-2026 platform params that the OpenAI adapter's `_STANDARD` set forwards
from client extras / deployment `extra_body`:
`prompt_cache_key`, `safety_identifier`, `store`, `verbosity`,
`web_search_options`, `prediction`, `modalities`, `audio`, `logit_bias`,
`service_tier`.

NIM strict-validates its request body and answered every request carrying one
of these with `400 Validation: Unsupported parameter(s): \`prompt_cache_key\``
— which made all Codex CLI (`/v1/responses`) traffic through a `nvidia-nim`
deployment fail, since Codex sends `prompt_cache_key` on every turn. The strip
is capability-driven and ignores `drop_params`, same as the adapter's existing
reasoning-key strip. The plain `openai` adapter is unchanged.

**Tests:** `tests/test_fix_round60.py` (5).

## Decoder hardening: typed-wrong upstream frames (AUDIT #153/#154) — 2026-09-16

`AnthropicAdapter.decode_stream_event` crashed with `AttributeError` on
syntactically-valid-but-typed-wrong SSE payloads (non-dict frames,
`message: null`, `usage: "x"`, `content_block: null`, `delta: null`,
`error: null`). The same frame class crashed the OpenAI-wire decoders on a
truthy non-dict `delta` or malformed `tool_calls` entries — and, worse, the
OpenAI/OpenRouter/NIM decoders *forwarded* non-string `content`/`reasoning`
as `TextDelta`/`ThinkingDelta`, a streaming-contract break that crashes the
pump (`len(d.text)`) or serializes an invalid chunk; NIM's variant fed the
non-string into `MiniMaxFramer.feed` (`str + int` TypeError escaping
`_feed_safely`). Gemini's nested `functionCall`/`usageMetadata` reads were
likewise unguarded. In every case the `AttributeError`/`TypeError` escaped
into the pump's generic handler: mid-stream `StreamError` to the client,
partial billing, `dep.record_fail`, and key cooldown for a frame carrying no
semantic content.

Fixed in all four decoder implementations (`anthropic_adapter.py`,
`openai_adapter.py` — inherited by openai-compatible/gmicloud/bai/cline/
workbuddy/opencode-chat — `openrouter_adapter.py` copy, `nim_adapter.py`,
`gemini_adapter.py`): type-guard per read, not per frame. Non-dict `delta`
decodes as empty (finish-only chunks must still reach finish handling);
malformed `tool_calls` entries are skipped; non-str `content`/`reasoning` are
dropped, not forwarded; NIM str-gates content before the framer; Gemini skips
typed-wrong `functionCall` parts and non-dict `usageMetadata`. Deltas
contract stays binding: adapters guarantee legality, encoders never defend.

**Tests:** `tests/test_fix_round61.py` (36, incl. an end-to-end streaming
smoke: an upstream injecting every poison frame mid-stream still completes
the client's Anthropic stream with no error event).

## OpenCode Zen: `union-alpha` route + universal client metadata — 2026-09-16

**File**: `wiwi/providers/opencode_adapter.py`

Two defects, both reported as `500 Internal server error` through a Zen
deployment (`provider_type: opencode`).

**1. `union-alpha` was routed to the wrong upstream protocol.** The
adapter's prefix table sent it to `POST {base}/chat/completions`
("everything else"). Zen's own endpoint table (`zen.mdx`, and the live
`https://opencode.ai/zen/v1/models`) puts it on the Anthropic Messages
protocol — `POST {base}/messages`, `@ai-sdk/anthropic`. Chat Completions
answers that model with a bare `500 Internal server error` envelope:

```
POST /zen/v1/chat/completions {"model":"union-alpha",...}
-> 500 {"type":"error","error":{"type":"error","message":"Internal server error"}}
```

which the gateway surfaced verbatim to the caller. `union-alpha` now joins
`_MESSAGES_PREFIXES`, so it encodes/decodes through the existing Anthropic
adapter like every other Messages model.

**2. The client metadata headers were gated to free models.** Round 29
added `x-opencode-session`/`x-opencode-client` for `-free` models only,
reasoning that paid models are not session-gated and that session ids shard
Zen's routing. The official client sends the full set on **every** request
to an `opencode`-provider model
(`packages/opencode/src/session/llm/request.ts`):

```
x-opencode-session: <sessionID>
x-opencode-request: <user.id>
x-opencode-client:  <flags.client>   # "cli"
x-opencode-project: <project.id>     # "global" fallback when unbound
```

and Zen's edge consumes all four for metrics and sticky routing
(`packages/console/.../zen/util/handler.ts`, including `$session`/`$project`
header substitutions into upstream requests). `headers()` now always sends
them; `union-alpha` — free, no `-free` suffix — was previously sent with no
session header at all, which the edge rejects with
`400 MissingSessionID` ("OpenCode's free tier can only be used in OpenCode").

The now-unused `is_free_model` classifier and `_last_model_id` state are
deleted rather than left as dead code.

**Tests:** `tests/test_fix_round63.py` (2, end-to-end through `create_app`:
Chat Completions → `/messages` with an Anthropic body, and a streamed
`input_json_delta` tool call decoded into Chat `tool_calls`);
`tests/test_fix_round29.py` migrated (14) — the free-only header tests and
the paid-model-omits-session test were replaced by universal-metadata tests,
`reset()` now pins rotation of both ids, and the classifier tests were
deleted with the helper.

**Live:** adapter → `POST /zen/v1/messages` with the metadata headers
returns `200` for `union-alpha` (non-stream, tool call parsed); the same
request without the session header returns `400 MissingSessionID`; the
pre-fix `/chat/completions` route returns `500`.

## Anthropic `/v1/messages` fidelity for Claude Code (AUDIT #156) — 2026-09-16

A Claude Code session routed through `/v1/messages` lost a set of features
silently. Found by driving the real CLI (2.1.273) against the gateway with a
mock Anthropic upstream, capturing the outbound request, and validating the
emitted SSE with the `anthropic` SDK. All items below are fixed.

### Header/body coupling — `anthropic-beta` never forwarded

Claude Code sends eleven betas on every request (`context-1m-2025-08-07`,
`interleaved-thinking-2025-05-14`, `context-management-2025-06-27`,
`effort-2025-11-24`, `mid-conversation-system-2026-04-07`, …). `grep
anthropic-beta` over `wiwi/` returned zero hits: the header was unread, and
`RequestContext` had no channel for it. Meanwhile the BODY fields those betas
authorize (`context_management`, `strict`, `output_config`) *were* forwarded —
Anthropic's documented hard-400 case.

`RequestContext.forward_headers` is the new channel. The server captures an
explicit allowlist (`server/app.py:_FORWARDABLE_HEADERS`) and
`Gateway._headers` merges it **last** (after adapter defaults, account
`extra_headers`, and deployment overrides) so an operator cannot pin a stale
beta list. Forwarding is restricted to Anthropic upstreams.

### `message_start.usage` was hardcoded to zeros

`dl.StreamStart` had no usage carrier, so the encoder emitted
`{"input_tokens": 0, "output_tokens": 0}` and discarded the adapter's real
counts. Claude Code's SSE scanner merges `message_start` usage by assigning
`cache_read_input_tokens`/`cache_creation_input_tokens` unconditionally and
copies only `output_tokens` from `message_delta`, so its running context total
stayed at zero for the whole session.

`StreamStart` now carries `prompt`/`cached`/`cache_creation`; the Anthropic
adapter populates them, `Gateway.stream` folds the upstream `StreamStart` into
its own instead of `continue`-ing past it, and the encoder emits them.

### `output_config.effort` was dropped

Only `output_config.format` was read, and `output_config` was not in
`_PASSTHROUGH_KEYS`. `/effort`, `--effort`, `CLAUDE_CODE_EFFORT_LEVEL` and
per-skill frontmatter all land in `output_config.effort`, so every effort
selection was a no-op while the session header still displayed it.

`GenParams.effort` is the new field. `effective_reasoning_effort` /
`effective_thinking_budget` reconcile the three dialect spellings
(`reasoning_effort`, `thinking_budget`, `effort`), which also fixed the same
drop on every non-Anthropic adapter — `nim`, `openrouter` and `opencode`
read only the raw `reasoning_effort`.

### Builtin suppression keyed on the tool *name*

`bt.is_builtin_name(t.name)` deleted any tool call whose name was
`web_search` — including a legitimate caller-defined function tool — and, in
the sync path, produced an empty `end_turn` turn. Suppression now keys on the
new `ir.ToolUsePart.builtin` flag, which the sync Anthropic decoder sets for
`server_tool_use` (it previously did not, so an identical upstream response
gave a clean stream but a phantom client tool call in non-streaming mode).
The same fix landed in `openai_chat` and `openai_responses` encoders.

### Stop-reason vocabulary collapsed into `stop`

`ir.StopReason` had no member for `pause_turn` (a provider-hosted tool loop
mid-flight — the client must re-send to continue), `stop_sequence` (which
caller-supplied sequence fired) or `model_context_window_exceeded` (an
overflow, not a completion). All three, plus `compaction`, became `end_turn`,
so Claude Code ended multi-search turns early and auto-compact could not see
an overflow. The literal is widened and both adapters use a shared
`_STOP_REASON_IN`/`_STOP_REASON_OUT` pair. A downgraded `tool_use` now also
clears `stop_sequence`, which was previously left set on a turn that did not
end by stop sequence.

### Other fixes

- **`count_tokens` blind to binary media.** `flatten_request_text` counted
  `p.url or p.file_id` and never read `b64`: a 300 KB screenshot reported 7
  tokens instead of ~1500. New `estimate_media_tokens` (per-format density)
  plus `estimate_request_tokens`, used by the route and by both usage
  fallbacks so the reported and billed numbers cannot drift. `_MODEL_ENCODING`
  gains a `claude` prefix so Claude models stop using chars/4.
- **`max_tokens: 0`** (Anthropic's cache pre-warm) was a falsy-or chain
  target and became `DEFAULT_MAX_TOKENS`, generating and billing up to 4096
  output tokens. Now tested for `None` explicitly.
- **Empty `system` blocks** were forwarded verbatim; Anthropic rejects them
  ("text content blocks must be non-empty"). The message path already filtered
  them; `_system_blocks_or_text` now does too.
- **Mid-conversation `role: "system"`** was rewritten to `user`, weakening the
  instruction and moving it out of its cache-prefix position. The role is
  preserved, and only *leading* system messages are hoisted to the top-level
  field.
- **`mcp_tool_use`** had no decode arm, so the call vanished while its
  `mcp_tool_result` survived — an unpaired result block. `ToolUsePart.block_type`
  now preserves the original spelling on replay.
- **Image/document `cache_control`** had no IR field at all, so a long-lived
  screenshot or PDF prefix was never cached and re-billed at 1x every turn.
- **Errored streams** left the open content block unclosed and skipped
  `message_delta`, so a client saw a `tool_use` block with unparsable input and
  no final usage. The error path now closes blocks, emits `message_delta`, and
  types the error from `StreamError.kind`/`status` (`timeout_error`,
  `overloaded_error`, `rate_limit_error`) instead of always `api_error`.
- **`ping`** was documented in `docs/API_REFERENCE.md` and `docs/STREAMING.md`
  but never emitted. `stream_ping_interval_s` (default 15s) now emits it while
  the pump is quiet, and `/v1/messages` sets `x-accel-buffering: no` (as
  `/admin/stream` already did).
- **Interleaved text/thinking was dropped entirely.** When content arrived
  while a tool_use block was open, the encoder returned `None` with no log and
  no counter. Anthropic blocks are strictly sequential so it cannot be emitted
  inline, but discarding it made the model's prose invisible to the user *and*
  absent from the replayed history, so the model could not see its own prior
  explanation. Such content is now buffered and emitted as its own block when
  the tool block closes (or at `final_frame` if the upstream never closes it).
- **Error bodies** now carry `request_id`, matching the real API.
- **Cross-provider:** Gemini encoded no `tool_choice` and no parallel control
  (new `toolConfig.functionCallingConfig`; parallel has no Gemini equivalent and
  now warns), and dropped `DocumentPart`; the OpenAI-family adapters dropped
  `DocumentPart` (now a `file` content part); OpenRouter's finish sweep failed
  to clear `_synthesized_opens`, the exact defect AUDIT #129 fixed in the base
  class — reproduced as an Open-less `ToolCallDelta`; opencode's Responses route
  ignored `disable_parallel_tool_use`.

**Tests:** `tests/test_fix_round64.py` (37, contract-level: usage on
`message_start`, flag-based suppression, sync/stream `server_tool_use` parity,
the full stop-reason matrix, error-path block closing and error typing, effort
across backends, `max_tokens: 0`, empty/mid-conversation system handling, media
counting, MCP pairing, Gemini `toolConfig` and documents, the OpenRouter leak).
Five existing tests that pinned the old behaviour were updated rather than
deleted: `test_anthropic_adapter_reads_stop_sequence`,
`test_anthropic_compaction_stop_reason`,
`test_anthropic_pause_turn_mapped_to_stop`,
`test_anthropic_happy_path_unharmed` (now expects usage on `StreamStart`), and
`test_anthropic_encode_response_suppresses_builtin_tool_calls` (now asserts the
flag contract, plus a new sibling proving a function tool named `web_search`
survives).

**Live:** the real Claude Code CLI (2.1.273) completed a turn through the
gateway against a mock Anthropic upstream, reporting the upstream's
`input_tokens`/`cache_read_input_tokens` in its `--output-format json` usage;
the eleven `anthropic-beta` flags now reach the upstream verbatim; a
`web_search_20250305` tool declaration survives to an Anthropic upstream and is
correctly dropped (not mangled) on an OpenAI backend.

---

## Round 67 — Claude Code tool search, effort precedence, and server-tool traces

Reported: a Claude Code session through the gateway reached for `Skill` and
`Task` unpredictably and `/effort` had no effect. The tool definitions were
never the problem — they round-tripped intact. What was missing was everything
Claude Code uses to *decide* which tool to reach for.

**IR** (`wiwi/ir/types.py`)
- `Tool.defer_loading: bool | None` — Anthropic tool search holds a deferred
  tool's definition out of context until a search discovers it. The API needs
  the definition server-side to expand `tool_reference` blocks, so the flag is
  sent on every request; dropping it loaded every deferred tool up front.
- `ToolResultPart.extra_blocks: list[dict]` — non-text, non-image blocks nested
  in a tool result, carried verbatim. `tool_reference` is the only channel
  through which tool search tells the model which deferred tool to load.
- `AssistantTurn.server_blocks: list[dict]` — provider-executed result blocks
  in emission order, for the sync path.
- `GenParams.effective_reasoning_effort` precedence reordered: an explicit
  `reasoning_effort`, then `effort`, then the budget-derived guess. `effort` is
  a deliberate caller selection (`/effort`, `--effort`,
  `CLAUDE_CODE_EFFORT_LEVEL`, per-skill frontmatter); letting
  `thinking.budget_tokens` win rewrote it to whatever the budget rounded to
  (8000 → `medium`) on every non-Anthropic backend.

**Builtin registry** (`wiwi/ir/builtin_tools.py`)
- `tool_search_bm25` → `tool_search_tool_bm25_20251119` (Anthropic) and
  `tool_search` (Responses); `tool_search_regex` →
  `tool_search_tool_regex_20251119` (Anthropic), also `tool_search` on
  Responses. Two canonicals because the query grammars differ (Python regex vs
  natural language) and re-encoding one as the other would tell the model to
  write patterns against a natural-language index.
- `_build_reverse` now lets the FIRST canonical claim an ambiguous wire type, so
  declaration order — not dict iteration luck — decides which canonical owns
  Responses' single `tool_search` spelling.

**Streaming taxonomy** (`wiwi/streaming/deltas.py`)
- New `ServerToolResultDelta(index, block, builtin)` carrying a
  provider-executed tool's result block whole. `ToolCallOpen.block_type` added
  so `mcp_tool_use` replays as `mcp_tool_use` rather than being flattened to
  `tool_use` (which leaves its `mcp_tool_result` unpaired).

**Codecs**
- `anthropic_messages`: decode `defer_loading`; collect nested non-text
  tool-result blocks into `extra_blocks` (previously kept only when the result
  carried no text at all, so the common "summary sentence + reference" shape
  lost the reference); `mcp_tool_use` joins `server_tool_use` in the
  builtin-tagged decode arm.
- `openai_responses`: decode `defer_loading`.
- `openai_chat`: decode `defer_loading` (Chat hosts no tool search, but a
  shared catalog re-encoded to a surface that does must keep it).

**Adapters**
- Anthropic: forward `defer_loading` on function tools (never on the search
  tool itself — the API rejects that); capture server-tool result blocks on
  both the sync and streaming paths; `mcp_tool_use` treated as
  provider-executed; re-emit `extra_blocks` as block-form tool-result content.
- OpenAI / Gemini / opencode: a provider-hosted call is no longer emitted as an
  unanswered function call. Its result rides as text instead, so the payload
  the model needs survives without creating a `tool_calls` entry (or a Gemini
  `functionResponse`) that nothing answers.
- OpenAI: `parallel_tool_calls` hoisted out of `if encoded_tools` (a request
  whose tools were all provider-hosted lost the constraint silently); explicit
  `parallel_tool_calls` now wins over `disable_parallel_tool_use`, matching the
  opencode adapter. `input_examples` rendered into the description where no
  native field exists.
- OpenRouter / opencode: same `input_examples` rendering; opencode also
  forwards `defer_loading` natively (Responses hosts tool search).
- Gemini: warns when `strict` is dropped instead of ignoring it silently.

**Encoders** (`wiwi/wire/anthropic_messages.py`)
- A `server_tool_use` is now BUFFERED until its result block arrives, then both
  are emitted as consecutive complete blocks — the shape the API itself
  produces. A call whose result never arrives is still discarded, so the A1
  invariant (never ship an unpaired `server_tool_use`) is preserved rather than
  weakened. Sync `encode_response` pairs the same way.

**Gateway** (`wiwi/core/gateway.py`)
- `_speaks_messages(dep)` replaces the `provider_type == "anthropic"` equality
  test for beta forwarding: the `opencode` (Zen) adapter serves `claude-*` over
  a genuine Messages endpoint and needs the caller's betas just as much.
- `_attempt_resume` copies `ctx.forward_headers` into the resume context. A
  beta-gated body field forwarded without its authorizing header is a hard 400,
  so losing them turned a recoverable stream error into a fatal one.

**Resume** (`wiwi/streaming/resume.py`)
- Provider-executed calls are excluded from the continuation message: the
  resume may land on a backend that cannot host them, and synthesizing a
  `tool_result` for one would create exactly the unpaired history the API
  rejects. `block_type`/`builtin` now survive tape replay.

**Tests:** `tests/test_fix_round67.py` (31). One pre-existing test
(`test_matrix_anthropic_client_receives_suppressed_trace`) pinned the old
discard-everything behaviour and was rewritten to the corrected pairing
contract; `test_anthropic_client_never_receives_a_half_pair` is its control.
Full suite 1910 passing, ruff clean.

## Zen free-tier anonymous access retired; `anthropic-version` scoped to Messages (2026-09-17)

> **Superseded by round 88 (2026-09-19).** The "`anthropic-version` scoped to
> Messages" half stands. The "anonymous free-tier access retired" half does
> not: keyless requests return 200 today. The 403 that led to that conclusion
> is Zen's free-tier **request-shape** gate, and the 2026-09-17 matrix missed
> it because it varied one factor at a time from a baseline that already failed
> the others — no `bash`/`read` tool payload, and a session id of the wrong
> length. All three conditions must hold *simultaneously*, so every row 403'd
> and each axis under test looked irrelevant, including the stream flag. See
> round 88 for the matrix that changes them together.

**Files**: `wiwi/providers/opencode_adapter.py`, `wiwi.yaml.example`

**Issue**: every keyless (`anonymous`) request to a `*-free` model failed with
`403 {"type":"error","error":{"type":"FreeTierError","message":"Error from
provider (Console): OpenCode's free tier can only be used from within
OpenCode"}}` — reported as "opencode rejected credentials (403)". Live probe
matrix the same day: anonymous gets that 403 on all three free routes (chat
`mimo-v2.5-free`, responses `muse-spark-1.3-contributor-free`, messages
`union-alpha`), with or without the extra attribution headers; the same
requests with a (fake) bearer clear the session gate and reach `401
AuthError`. So the `x-opencode-*` spoof still passes — Zen retired anonymous
keyless free-tier access (which returned 200 until 2026-09-16), and free
models now need a valid `OPENCODE_API_KEY` like paid ones. Separately,
`headers()` sent `anthropic-version` on every route although it names
Anthropic's Messages API version.

**Before**:
```python
h = {"User-Agent": ..., "HTTP-Referer": ..., "X-Title": ...,
     "anthropic-version": "2023-06-01"}  # on chat/responses/gemini too
```

**After**:
- `anthropic-version` is added only when `self._last_route == "messages"`
  (set by `build_url`/`encode_request` before `headers()` on the hot path).
- Module docstring, sentinel comments, and `wiwi.yaml.example` now record
  that anonymous free-tier traffic 403s upstream and free models require a
  real key. No `core/` change: error mapping stays generic per the
  hub-and-spoke rule.

**Tests**: `tests/test_fix_round71.py` (4) — route table for
`muse-spark-1.3-contributor-free`, per-route `anthropic-version` presence
with fingerprint intact, Responses body shape, Responses usage decode
(prompt/completion/cached/reasoning). Gateway E2E verified live-path via
mocked `POST /zen/v1/responses` (correct body, spoof headers, usage with
`cached_tokens`/`reasoning_tokens`); live upstream re-probed post-fix
(anon → 403 policy, fake key → 401, spoof intact).

## Entitlement 401/403 misclassified as credential failures (2026-09-17)

**Files**: `wiwi/providers/base.py`, `wiwi/providers/opencode_adapter.py`,
`wiwi.yaml.example`

**Issue**: a request to `muse-spark-1.3-contributor-free` through the `io`
deployment failed with
`io rejected credentials (403): Error from provider (Console): OpenCode's free
tier can only be used from within OpenCode`, and the log showed two pool keys
(`io/3`, `io/4`) marked rejected by a request that never reached an auth check.
The wording and the key-pool damage both said "bad key"; the real cause is a
Zen account policy.

**Root cause (live probe matrix, 2026-09-17):** Zen's Console returns
`403 FreeTierError` for a free model on *any* request whose workspace has no
paid entitlement — keyless (`anonymous`) and with a valid bearer alike. A
bearer only clears the session gate and then hits the billing gate
(`401 CreditsError: No payment method`, i.e. the key itself is fine). No
header variation changes this (project id, client tag, `x-zen-model`,
`x-zen-billing-source`, ULID vs hex ids, stream flag, browser UA: all 403), so
the `x-opencode-*` client spoof is complete and correct — the free tier is
simply gated on a paid workspace, and keyless anonymous access (200 until
2026-09-16) is retired.

> **The last sentence is wrong — see round 88 (2026-09-19).** Keyless free-tier
> access was never retired; it returns 200 today. Every row of that matrix
> started from a body that already failed the gate's tool-payload and
> session-length conditions, so the gate answered 403 no matter which *other*
> factor was varied — the stream flag and id format included. The
> classification this entry actually shipped (402/403 `permission_error`, key
> pool not charged) remains correct and useful.

The bug wiwi owned: `error_from_provider_status` mapped **every** 401/403 to
`authentication_error`, so `status_for_key_pool` reported it to the pool and
`ProviderAccount.on_result` ran `err_count += 2` (retire after
`key_max_consecutive_fails`). One account-level policy rejection therefore
burned two healthy keys per attempt and cooled the whole provider — a
self-inflicted outage on top of the upstream refusal.

**Fix (`wiwi/providers/base.py`):**

- `_extract_error_type()` reads the machine `type`/`code` from Zen/Anthropic
  (`{"type":"error","error":{"type":"FreeTierError"}}`) and OpenAI-shaped
  bodies; `_entitlement_kind()` splits account refusals into **billing**
  (`FreeTierError`, `CreditsError`, `MonthlyLimitError`, `UserLimitError`,
  usage-limit text) and **policy** (region, data-policy, `ModelError`).
- `error_from_provider_status` maps billing refusals to **402
  ``permission_error``** and policy refusals to 403 ``permission_error``, both
  with `requires billing (…)` / `denied access (…)` wording instead of
  `authentication_error` / `rejected credentials`. The 402 matters because
  clients render *every* 401/403 as "re-enter your API key" — Cline's SDK
  explicitly classes 401/403 as credential rejections — so a working key was
  being blamed for a billing gap. Retryable stays True so the request still
  fails over.
- `status_for_key_pool` returns `None` for a `permission_error` 401/403/402, so
  the key is never charged. A plain 403 (`forbidden`, no marker) stays an
  authentication failure, and a genuine `AuthError`/`Invalid API key` body keeps
  its historical semantics.
- `opencode_adapter` docstring + `wiwi.yaml.example` record the retired
  keyless free tier (free models need a real `OPENCODE_API_KEY` *and* a funded
  workspace).

**Tests**: `tests/test_fix_round71.py` (11) — route/header scoping, Responses
body/usage, and the entitlement classification: FreeTierError 403 → 402
`permission_error` + `None` pool status; billing 401 markers → 402; policy
markers → 403; phrase fallback; plain 403 still auth-classified; real
credential 401 still marks the key unhealthy. Full suite 1927 passing (13
pre-existing `ratelimit/memory.py` failures unrelated to this change), ruff
clean.

## Zen's Messages route needs `x-api-key`, not `Authorization` (AUDIT #176) — 2026-09-17

**File**: `wiwi/providers/opencode_adapter.py` (`headers()`)

Zen serves four wire protocols from one base URL, and they do **not** share an
auth scheme. `headers()` built a single dict for all of them and always
emitted `Authorization: Bearer`. That is right for `/chat/completions` and
`/responses` (OpenAI-wire) and wrong for `/messages`, which is Anthropic-wire
and reads `x-api-key` — a Bearer token is simply not seen by that front end:

```
POST /zen/v1/messages  Authorization: Bearer sk-…   {"model":"claude-sonnet-5"}
-> 401 {"type":"error","error":{"type":"AuthError","message":"Missing API key."}}
```

"Missing", not "Invalid": the credential never arrived. Verified live
2026-09-17 with a real Zen key — the same request with `x-api-key` instead
gets `401 CreditsError "No payment method"`, which is the *next* gate, past
authentication. So the header swap is what moves the request from "not
authenticated" to "authenticated, unbilled".

**Fix** — auth follows the wire, not the adapter:

```python
if self._last_route == "messages":
    h["x-api-key"] = key.secret.strip()
else:
    h["Authorization"] = f"Bearer {key.secret.strip()}"
```

The `anonymous` sentinel still omits every credential on every route. This
mirrors `AnthropicAdapter.headers()` (`anthropic_adapter.py:203`), the adapter
this route already delegates its encode/decode to, and the module docstring now
says so.

### Follow-up: the Gemini route had the same bug, and the "querystring" story was wrong

Review caught that the first pass documented the Gemini route as keeping "its
querystring key", and that claim was **false** — while asserting it, the same
defect was still live on that route.

Zen's Gemini front end reads **`x-goog-api-key`**. Live probe 2026-09-17, real
Zen key, `gemini-3-flash`:

| credential | result |
|---|---|
| `Authorization: Bearer` (what we sent) | 401 `AuthError` "Missing API key." |
| `x-goog-api-key` | 401 `CreditsError` "No payment method" |
| `?key=` querystring | 401 `AuthError` "Missing API key." |
| none | 401 `AuthError` "Missing API key." |

The querystring carried no key either: `wiwi/core/recovery.py:190` appends one
only when `provider_type == "gemini"` and this deployment's type is
`"opencode"`, and `OpencodeAdapter.build_url` returns a bare
`...:generateContent` with no `?key=` placeholder, so the
`url.endswith(("?key=", "&key="))` branch never fires. `GeminiAdapter.headers()`
returning `{}` is irrelevant — the opencode adapter never calls it, it builds
its own dict. So the route sent a credential upstream could not read, and the
querystring claim compounded the error rather than describing the behavior.

**Fix**: replace the two-branch `if` with a route→header table
(`_CREDENTIAL_HEADER`), so the scheme is data and a fifth route cannot silently
inherit the wrong one:

```python
_CREDENTIAL_HEADER: dict[Route, str] = {
    "messages": "x-api-key",
    "gemini": "x-goog-api-key",
    "chat": "Authorization",
    "responses": "Authorization",
}
...
scheme = _CREDENTIAL_HEADER.get(self._last_route, "Authorization")
value = key.secret.strip()
h[scheme] = value if scheme != "Authorization" else f"Bearer {value}"
```

Post-fix live check, all three routes with a real key: gemini → `CreditsError`,
messages → `CreditsError`, chat → `403 FreeTierError` (correct for a free
model). Every route now clears authentication.

**Also pinned**: a **streaming** messages-route case. Round 72's end-to-end
coverage used the non-streaming `_call_once` site, but the stream pump builds
headers at its own site with its own 401-refresh rebuild and is the path every
Claude Code client takes. The pre-existing streaming messages test
(`tests/test_fix_round63.py`) used `key="anonymous"`, so it could not catch a
scheme regression — the exact blind spot that let the original bug ship.

**Tests**: `tests/test_fix_round73.py` (7) — gemini `x-goog-api-key`, cross-route
scheme isolation in both directions, anonymous sentinel, fingerprint and
`anthropic-version` scoping, gateway end-to-end on the gemini route (asserting
the key is absent from the URL too), and the streaming messages case. RED before
the fix (3 gemini tests failed; the streaming control passed, confirming round
72 already covered that site); GREEN after.

**Why it hid this long**: every Messages-route test in the suite configured
`key="anonymous"` (`tests/test_fix_round63.py`), which omits credentials
entirely — so the wrong-scheme branch was never exercised with a real key.
Round 71 asserted `anthropic-version` placement on that route but not the auth
header. It is masked in production too: the `io` account is on an unbilled
workspace (all six keys → `CreditsError` on paid models) and free models are
refused `403 FreeTierError` on every route and every live key (9/9 probed), so
Messages traffic fails today for a *billing* reason and would have gone on
failing for an *auth* reason once payment was added.

**Not a code bug** for the auth scheme. "The free tier is not recoverable by
any header combination" is true as stated — none of the eight header variants
mattered — but the conclusion drawn from it was wrong: what recovers the free
tier is the request *body* and the session id format, which round 88 supplies.
Paid models on an unbilled workspace do stay unreachable until a payment method
is added. See #267.

**Tests**: `tests/test_fix_round72.py` (8) — per-route auth scheme for
messages (union-alpha, claude-sonnet-5) with chat/responses/gemini controls
asserting `x-api-key` is *absent* there, the anonymous-sentinel control, the
round-29 fingerprint and round-71 `anthropic-version` preservation, and
end-to-end through `create_app` on both the messages and chat routes. RED
before the fix (3 messages-route tests failed, 5 controls passed); GREEN
after. Full suite 1936 passing (same 13 pre-existing `ratelimit/memory.py`
failures), ruff clean.

## Entitlement refusals must still reach the proxy log (round-74 follow-up to AUDIT #174) — 2026-09-17

**File**: `wiwi/router/router.py` (`execute_with_retries`)

Round 71 made `status_for_key_pool` return `None` for entitlement refusals
(`FreeTierError`/`CreditsError` → 402/403 `permission_error`) so the key pool
is never charged. But `execute_with_retries` gated its proxy-log warn on that
same value (`if status is not None: _proxy("warn", ...)`), so the fix silenced
the one line that told the operator *why* requests were failing over: an
operator tailing the proxy log during the free-tier 403 storm saw requests
fail with no upstream-status line at all.

**Fix**: the key-pool charge stays gated on `status` (a `None` must never
reach `on_result` — that would be the #174 regression again), but the warn is
now split:

- `permission_error` on 401/402/403 →
  `io refused zen/muse-spark-1.3-contributor-free [main] — account entitlement (402): io requires billing (403): ...`
- everything else keeps the historical
  `upstream <status> on <group>/<model> [<provider>/<key>]: <message>` shape.

A caller-side 400 (`status_for_key_pool` → None, non-permission) still logs
nothing, matching the pre-existing contract.

**Tests**: `tests/test_fix_round74.py` (6) — entitlement warn line names
provider/group/key/cause at warn level for both the 402 billing and 403 policy
halves; `err_count` stays 0 and the key stays `active` across repeated
refusals (num_retries=3); genuine `AuthError` keeps the historical shape AND
the double-count charge; caller 400 stays silent; upstream 5xx keeps the
historical shape. GREEN; full suite + ruff re-run below.

## Finished the round-70 rate-limiter fix (missing helpers + global refund) — 2026-09-17

**File**: `wiwi/ratelimit/memory.py`

Round 70's `record_tokens` rewrite landed half-applied: it called
`_find_event` / `_newest_estimated`, which did not exist, so every TPM
reconciliation raised `AttributeError` (13 failing tests across rounds
2/6/48/66/69/70 and `test_audit_fixes`). Completed the intended semantics,
mirroring `Deployment.settle_tokens`:

- `_find_event(w, request_id)` — any event with that id, estimated or
  confirmed, so a repeated reconcile *adjusts* instead of double-counting.
- `_newest_estimated(w)` — the lenient fallback, now reachable only from the
  id-less path.
- `release("")` no longer returns before the global loops: `check("")`
  reserves in `global:rpm`/`global:tpm`, so an early return leaked one of every
  global slot for the window; only the key-scoped `":rpm"`/`":tpm"` lookups are
  skipped when `key_id` is empty.
- `test_fix_round70.py`'s admission test claimed a request "must be admitted"
  when the window was already at cap (300 + 700 == 1000); corrected to assert
  refusal — the refusal is the behaviour the fix exists to guarantee, and the
  300-token probe is exactly the one the bug let in.

Full suite: 1964 passed, ruff clean.

## Round 75/76/78 — codec, adapter, and streaming-internals fixes (AUDIT #159-#211) — 2026-09-18

**Files**: `wiwi/wire/anthropic_messages.py`, `wiwi/wire/openai_chat.py`,
`wiwi/wire/openai_responses.py`, `wiwi/providers/anthropic_adapter.py`,
`gemini_adapter.py`, `openai_adapter.py`, `openrouter_adapter.py`,
`nim_adapter.py`, `nim_tool_schema.py`, `wiwi/core/gateway.py`

Eleven findings, one root cause each time: a `.get(key, default)` that defends
against a *missing* key but not against a JSON `null`, an emission that was
never made exactly-once, or an unguarded nested read on caller-controlled JSON.
The poisoned value then surfaced far from its origin — mid-stream after the
client already had a 200, on the *next* turn of the conversation, or in the wire
encoder as an undispatchable `tool_use`.

### Anthropic Messages codec (`wiwi/wire/anthropic_messages.py`)

- **#178** `_emit_server_call` closed the wrong content block. When a client
  `tool_use` block was open and interleaved text/thinking had been deferred,
  `_flush_deferred()` opened a NEW text block and bumped `_block_idx`, and the
  server call then used that index for its own start/stop — so the text block
  was opened and never stopped and the server call received two stops. With
  thinking, a later `signature_delta` was stamped onto the *result* block.
  New `_drain_deferred()` flushes and then closes the block the flush opened;
  the identical sibling in `feed`'s `ServerToolResultDelta` arm (no buffered
  call) got the same treatment. One start and one stop per index now.
- **#167** an assistant turn with `content: null` was silently dropped (the
  `if parts:` guard had no assistant arm), corrupting turn alternation on
  replayed history — `content: null` is what the API itself emits for a
  tool-use-only turn. Now appends an empty-parts assistant message, matching
  `openai_chat.py` exactly.
- **#168** a malformed image block (`source.type == "base64"` with no `data`)
  was forwarded upstream as `"data": null` instead of being dropped. All three
  image decode sites now skip a missing/non-string payload.
- **#186** explicit JSON `null` for `name`/`description` passed the
  `get(k, "")` default (which only covers a MISSING key). Coerced at the decode
  boundary with the file's existing `isinstance` idiom.
- **#187** typed-wrong `media_type` reached the wire verbatim, and the
  OpenAI/Gemini adapters interpolate `mime` into a `data:` URL. Now falls back
  to the documented default.

### OpenAI Chat / Responses codecs (`wiwi/wire/openai_chat.py`, `openai_responses.py`)

Two new module-level helpers carry the policy; `openai_responses.py` imports
both.

- **`_str_or_empty(raw)`** — applied to `input_text`/`output_text`/`text`
  blocks, `function_call` names and raw arguments, tool `name`/`description`,
  both `tool_choice` name sites, `DocumentPart.filename`, and the
  unknown-hosted-tool fallback. Fixes **#184** (a non-string `text` reached
  `TextPart.text` typed as `str`, and the fallback estimator's `" ".join`
  raised a 500 *after* the upstream was billed) and **#186**.
- **`_stop_list(raw)`** — a bare string becomes one sequence, a list is
  filtered to its `str` items, anything else becomes `[]`. `(body.get("stop")
  or [])` let any truthy non-list through, so `{"stop": true}` reached the
  upstream verbatim (**#185**; AUDIT #127's shape one level up).
- **#165** `_builtin_query` returns `""` unless `orjson.loads` produced a dict —
  a well-formed JSON scalar or array parses cleanly but has no `.get`, so a
  hosted `web_search` call whose upstream streamed `[1]` crashed the encoder
  mid-stream.
- **#168** `_decode_image` returns `None` for an empty url, not only a
  non-string one (an absent `image_url` produced `data:image/png;base64,None`).
- **#166** the Chat decoder normalizes `role` to the known set, defaulting to
  `"user"`, mirroring `anthropic_messages.py`.
- **#187** `top_k` goes through `ir.coerce_int`, matching `max_output_tokens`.

### Provider adapters (`wiwi/providers/`)

- **#160** `AnthropicAdapter`'s `input_json_delta` arm now gates `partial_json`
  with `isinstance(..., str)` like its `text_delta`/`thinking_delta` siblings; a
  null fragment became `ToolCallArgsDelta(args_fragment=None)` and the gateway's
  `"".join()` raised mid-stream.
- **#161** `GeminiAdapter` gains `_saw_tail`, set on first tail emission in both
  the `if finish:` and the `elif u and not parts:` arm and cleared in `reset()`.
  A parts-less usage-bearing intermediate frame followed by a finish frame
  previously emitted two UsageFinal/Finish/StreamEnd, and every consumer keeps
  the last value — so the stream was billed on the *intermediate* frame.
- **#194** a module-level `_token_count()` (built on `ir.coerce_int`, which
  already rejects bools — `true` is not a token count) is applied at every
  counter read site in the four adapters, streaming and non-streaming, plus
  Anthropic's `message_start` counters and `thinking_tokens`. `cline`,
  `workbuddy` and `bai` inherit the guard through `super()`.
- **#195** the unguarded `candidatesTokenCount + thoughtsTokenCount` sum in both
  Gemini decoders now coerces each operand — the strongest instance of #194,
  where the `TypeError` came out of the decoder itself.
- **#196** `NimAdapter`'s reused-index branch now flushes the deferred
  `_pending_opens.pop(idx)` as a `ToolCallOpen` before the `ToolCallClose`,
  mirroring `OpenAIAdapter`. NIM alone diverged, so `Close(0)` reached the
  encoder before any `Open(0)` and `AnthropicStreamEncoder` dropped it, leaving
  `content_block_start` with `name: ""`.
- **#197** `OpenRouterAdapter`'s streaming `usage` read is `isinstance(u, dict)`
  -gated like its siblings, and `reasoning_details`' `text`/`summary`/`data`
  are coerced in both decoders — a null text decoded to `ThinkingPart(text=None)`
  and the next turn's replay (`reasoning += p.text`) raised on all six Chat-wire
  adapters.
- **#200** OpenRouter's mid-stream error message is coerced, matching
  `ClineAdapter`; a null previously reached the client as `"message": null`.
- **#201** `nim_tool_schema` resolves alias collisions by fixed-point
  displacement. A tool declaring both `type` and `_nim_arg_type` lost the second
  property (the model was told there is ONE parameter; `required` named it
  twice). Note the suffix form `_nim_arg_type_2` is NOT a valid resolution:
  `collect_nim_tool_aliases` strips exactly one prefix, so it reverses to
  `type_2` — a name the tool never declared. Every alias is therefore kept as
  `_nim_arg_` + its original, which is exactly invertible.

### Gateway (`wiwi/core/gateway.py`)

- **#159** a mid-stream resume emitted a SECOND `StreamStart`: `started` is a
  local of `Gateway.stream` initialised once before the consumer loop, and the
  resume branch `continue`s back into it, while the resumed pump runs a new
  adapter instance that emits its own frame from `message_start`. An Anthropic
  client reads a second `message_start` as a NEW message. The arm now yields
  only when `!started`. The resume's counts are deliberately NOT summed into the
  opening frame — the continuation request replays the original prompt *plus*
  the partial output, so its prompt count already contains the first attempt's
  tokens; summing would double-count on the client's context meter. The numbers
  ride in the single terminal `UsageFinal` via `accumulated_stream_usage`, and
  the new `usage_fallback()` uses the provider-reported opening counts for
  billing when an attempt dies before its `message_delta`.
- **#211 (Critical)** the pump's error handler was unguarded: the mid-stream
  `except Exception` arm, the idle-timeout arm and the clean-completion usage
  fallback awaited `_note_stream_failure` / `_price_partial` /
  `queue.put(StreamError(...))` with no `try/except`, so any fault killed the
  pump before the terminal frame was queued and the consumer parked on
  `await queue.get()` **forever** — no terminal frame, no timeout. New
  `_put_frame` (non-blocking, with a bounded fallback for a genuinely full
  queue) and `_fail_stream` (guards the accounting awaits independently, queues
  the terminal frame last, idempotent via `terminal_sent`); `_close_upstream()`
  moved into a `finally`. The same never-completing shape existed before
  `_pump_once`'s own try, where a fault stranded `call_one` on
  `await ready.wait()` — `ready` is now set in every pre-connect arm with a
  `_pump` backstop. `usage_fallback()` is shared with `_complete_via_stream` so
  the two fallbacks cannot drift.
- **#184** `flatten_request_text`'s join now coerces per element (one pass at the
  join, not a guard per append); `None` maps to `""` so an unset optional field
  adds no phantom token, and well-formed input is byte-identical.

**Tests**: `tests/test_fix_round75.py` (12), `test_fix_round76.py` (15),
`test_fix_round77.py` (34 tests / 116 parametrized cases), `test_fix_round78.py`
(36), `test_fix_round79.py` (34). RED evidence per finding in each agent's
report; full suite 2259 passed, ruff clean.

## Round 85 follow-up — streaming guards, OAuth rotation, and accounting visibility (AUDIT #162-#211) — 2026-09-18

The remainder of the round-75 sweep: defects outside the codecs but still on the
translation path (the streaming contract, the OAuth refresh seams, and the
operator surfaces that report whether any of it worked).

### Streaming contract (`wiwi/streaming/`)

- **#191 (High)** `JournalStore.path_for` sanitized by *stripping* disallowed
  characters, so distinct ids collided onto one file (`a/b`, `a.b`, `a b`,
  `a!b` all resolved to `ab.jsonl`). The request id is returned to every client
  in `x-wiwi-request-id`, so appending a single `.` to a known id resolved to
  the victim's exact journal path — re-opening the #67 cross-key disclosure
  through a different door. A conforming id (`[A-Za-z0-9_-]{1,64}`, the
  `uuid4().hex[:16]` shape) keeps its historical filename so existing on-disk
  journals stay replayable; anything else is hashed to `h<sha256>.jsonl`, and
  the `h` prefix keeps the two naming spaces disjoint.
- **#192 (High)** `validate_tool_args` guarded only the *top-level* schema. A
  malformed nested keyword — `properties` as a list/string, `required` as a
  non-list — raised out of the pump's mid-stream handler, which cooled the
  deployment's key for every other user AND killed the caller's own stream
  (HTTP 200, some content, then an error frame and no `finish_reason`). Now
  coerced at the seam, and an unhashable `required` member is skipped rather
  than raising from the `in` lookup.
- **#193** `_repair_truncated_json` stripped a `\uXXXX`-shaped tail
  unconditionally, so an **even** backslash run (a literal escaped backslash)
  before it lost the entire tool-argument object — a model emitting a Windows
  path or regex cut mid-string silently degraded to `{}`. The surrogate block
  is now gated on the same odd-run parity test the partial-escape branch
  already computed. A second half of the same defect was found and fixed: the
  low-surrogate branch's predecessor check used an unparity-checked regex, so a
  literal `uD83D` followed by a real low-surrogate escape kept a lone `U+DE00`
  that `json.loads` accepts but `orjson.dumps` rejects.

### OAuth refresh seams (`wiwi/providers/`)

- **#169 (High)** Cline's on-demand 401 hook built its OWN `ClineAutoRefresh`
  worker, so it shared neither the sweeper's per-provider lock nor its circuit.
  Refresh tokens rotate (each refresh consumes the old one), so a sweeper tick
  and a client 401 in the same window both read the same token and both POST;
  the loser's `invalid_grant` maps to `mark_dead`, taking the provider down
  until a human re-authenticates. Both paths now funnel through one lock-guarded
  `_refresh_locked` (exposed as `refresh_now`), which re-reads the circuit and
  the stored record under the lock and checks a per-provider generation counter
  captured before `acquire()` — a sibling that rotated while this caller waited
  returns "already fresh, retry" instead of burning the token. The identical
  defect existed in `workbuddy_auto_refresh.refresh_for_provider` and is fixed
  the same way.
- **#170** WorkBuddy's sweeper used `expires_within_lead` as its ONLY due-check,
  and that predicate returns True for an unknown expiry — so a stored record
  with no `expiresAt` was refreshed every 60 s forever, one POST and one DB
  write per minute, each consuming a rotating token, and any transient failure
  could `mark_dead` a key that had never expired. The lead predicate now
  returns False for an unknown expiry; the admin listing's `needs_refresh`
  keeps its "unknown = needs attention" meaning deliberately, so
  `/admin/workbuddy/accounts` still flags such an account. The Cline and
  WorkBuddy sweeps now agree.

### Accounting visibility (`wiwi/logging_core/`, `wiwi/server/`)

- **#171 (High)** a failed request-log DB write discarded the batch while
  `dropped_request_logs` stayed at 0 — so `/health` and `/metrics` reported
  "healthy" while every durable row was lost. The failure now counts the batch
  (`failed_request_log_writes`).
- **#173** proxy-queue-full drops and failed audit writes were uncounted
  entirely; both now have counters, plus a `dropped_log_events` sum. Four
  counters rather than one shared one, deliberately: a saturated log pipeline
  and an unavailable DB need different remedies.
- **#180** the `counter`-declared Prometheus families were recomputed from a
  500-event ring each scrape, so they DECREASED as events evicted —
  `wiwi_cost_total` read 0.5 → 500 → 0.5 across three scrapes while the gateway
  only ever served more traffic, and `rate()` treats each eviction as a process
  restart. Process-lifetime `RequestTotals` (folded in at accept time, so a
  queue drop cannot under-report) now carries every counter family; all
  documented metric NAMES are unchanged, and the three quantile families stay
  `summary`.
- **#179 (High)** `record_spend`'s `except Exception: return True` reported a
  failed charge as success, so a hard budget cap was unenforced for as long as
  the write path failed. The failure is now counted (`spend_charge_failures`,
  on `/health` and `/metrics`), logged, and retried through the unconditional
  true-up; only when that also fails does it refuse. The upstream already
  served and billed the request, so refusing on a repairable write failure
  would be the AUDIT #24 defect with a different status code.

### Server surfaces (`wiwi/server/app.py`)

- **#177 (High)** the Anthropic `ping` keep-alive was yielded by the pump as
  raw `bytes`, but every consumer routes each item through `encoder.feed(d)`,
  which type-checks on delta classes only — a `bytes` argument fell through
  every branch and returned `None`. `stream_ping_interval_s` (default 15 s) was
  therefore a complete no-op and an idle proxy still reaped long thinking
  turns. `_stream_response` now passes `bytes` straight to `_emit` (still
  journaled with a sequence id).
- **#181** provider delete/rename mutated in-memory routing BEFORE the DB write,
  so a failed write answered 500 while having taken effect in memory only — the
  provider and its plaintext key returned after a restart, and `log_audit` was
  never reached. Both paths now persist first, matching `POST /admin/providers`.
- **#183** `/health` reported the constant `"ok"` regardless of state, so a
  container with zero usable providers stayed "healthy" to the Docker
  HEALTHCHECK. `status` is now derived (`degraded` when there are no providers
  or no available group); HTTP stays 200 so liveness remains separate from
  readiness.
- **#199** the admin `reset_status` cleared status but not `err_count`, so the
  very next failure re-retired the key; it now calls the router's
  `recover(force=True)`, which is the operator's unconditional reset.

### Gateway and adapters

- **#159** a mid-stream resume emitted a SECOND `StreamStart` (a second
  `message_start` to an Anthropic client, which reads it as a NEW message). The
  resume's counts are deliberately NOT summed into the opening frame — the
  continuation replays the original prompt *plus* the partial output, so its
  prompt count already contains the first attempt's tokens.
- **#211 (Critical)** the pump's error handler was unguarded, so any fault in
  `_note_stream_failure`/`_price_partial`/`queue.put` killed the pump before the
  terminal frame and the consumer parked on `await queue.get()` **forever** —
  no terminal frame, no timeout. Made total, with `_close_upstream()` in a
  `finally`; the same never-completing shape before `_pump_once`'s try is fixed
  too.
- **#160/#161/#194/#195/#196/#197/#200** adapter decode guards: a JSON `null`
  reaching a typed field, a duplicated terminal tail, and a `ToolCallClose`
  with no preceding `ToolCallOpen`.
- **#162 (High)** revoking a credential was undone by an in-flight
  `authenticate()` re-caching its stale info; an eviction-generation check now
  discards the stale read.
- **#163/#198/#202** `rpm: 0` no longer means "unlimited"; the per-owner key cap
  is no longer a check-then-act race; `ttl_seconds: 0` means the same thing on
  create and update.
- **#190 (High)** `Deployment.settle_tokens` adopted another request's
  reservation, under-counting the per-deployment TPM/RPM cap — the same defect
  rounds 66/70 removed from `ratelimit/memory.py`. The resolver now mirrors
  that module.

## Round 68 (2026-09-18) — hosted-item fidelity and the in-band error frame

Two defects on the translation layer, both found by a producer/consumer audit
of the `IRStreamDelta` taxonomy.

### In-band upstream error frames were dropped on the OpenAI/NIM/B.A.I routes

**Files**: `wiwi/providers/openai_adapter.py`, `wiwi/providers/nim_adapter.py`
(B.A.I inherits via `class BAIAdapter(OpenAIAdapter)`).

An OpenAI-compatible upstream that reports a mid-stream failure in-band as
`{"error": {...}}` with **no `choices` array** had that frame discarded:
`decode_stream_event` built its delta list from `usage` + `choices` only, so the
frame produced `[]`.

The damage was downstream. With no terminal delta the pump saw
`finish is None and not saw_terminal` and reported
`StreamError("upstream stream ended without completion", "connection")` **and**
called `_note_stream_failure` — cooling a healthy deployment and feeding the
key's error streak for an error the upstream had reported cleanly. This is the
same downstream mechanism as AUDIT #76 (Gemini omitting `finishReason`), from a
different trigger: there the decoder returns `[]` for a shape it does not
model, here for one it deliberately ignored.

OpenRouter already mapped this shape (`openrouter_adapter.py:277-281`); Gemini,
Cline and WorkBuddy map their own variants. OpenAI, NIM (its own copy of the
decode loop, not a delegate) and B.A.I did not.

**Before** (both files, after the `usage` parse):

```python
choices = chunk.get("choices") or []
if not choices:
    return out
```

**After** — the guard runs *before* the usage parse, matching OpenRouter's
ordering, so a failed frame cannot contribute a partial token count to cost:

```python
err = chunk.get("error")
if isinstance(err, dict):
    out.extend(self._flush_open_tools())
    out.append(dl.StreamError(
        message=str(err.get("message") or "upstream stream error"),
        kind="status",
        etype=err.get("type") if isinstance(err.get("type"), str) else None))
    return out
```

The provider's own `type` is preserved as `etype` because a mid-stream frame
carries no HTTP status, so `kind`/`status` alone cannot recover the
classification a client retries on.

**Tests**: `tests/test_fix_round68.py` —
`test_openai_adapter_maps_in_band_error_frame_to_stream_error`,
`test_openai_adapter_in_band_error_preserves_provider_type`,
`test_nim_adapter_maps_in_band_error_frame_to_stream_error`,
`test_error_frame_does_not_also_emit_a_usage_final` (pins the ordering), and
`test_openai_adapter_still_decodes_a_normal_chunk_after_the_error_guard` as the
control. AUDIT #212.

### A hosted tool-search call was rendered as a web search on the Responses surface

**File**: `wiwi/wire/openai_responses.py`.

`_builtin_call_item` hardcoded `web_search_call` for **every** hosted builtin.
But the registry maps two distinct canonicals onto the Responses surface —
`web_search` → `web_search`, and `tool_search_bm25`/`tool_search_regex` →
`tool_search` — and the OpenAI SDK models these as **separate output items**:

| | web search | tool search |
|---|---|---|
| SDK type | `ResponseFunctionWebSearch` | `ResponseToolSearchCall` |
| wire `type` | `web_search_call` | `tool_search_call` |
| payload | `action: {type, query}` | `arguments`, `execution`, `call_id` |

So an Anthropic `tool_search_tool_bm25_20251119` step (Claude Code's tool
search) reached a Responses client labelled as a web search — the client
rendered a search that never happened and lost the real step entirely.

**Before**:

```python
def _builtin_call_item(item_id: str, query: str) -> dict[str, Any]:
    return {"type": "web_search_call", "id": item_id, "status": "completed",
            "action": {"type": "search", "query": query}}
```

**After** — the registry decides, so the mapping lives in `builtin_tools.py`
rather than as a name list here. `_builtin_is_tool_search` accepts either the
canonical name or the Anthropic wire spelling, since `ToolCallOpen.builtin`
carries whatever the provider called the block:

```python
def _builtin_is_tool_search(builtin: str | None) -> bool:
    if not builtin:
        return False
    canonical = bt.canonical_for("anthropic", builtin) or builtin
    return bt.wire_type_for("openai_responses", canonical) == "tool_search"
```

All three render sites were updated: the sync `encode_response`, the stream
`_close_tool`, and the stream `response.output_item.added` (which opens the
item with its own shape and needs the matching `ts_` id prefix). A real web
search is unchanged, pinned by a control test.

**Tests**: `tests/test_fix_round68.py` —
`test_responses_sync_labels_tool_search_as_tool_search_call`,
`test_responses_web_search_still_renders_as_web_search_call`,
`test_responses_tool_search_item_matches_the_sdk_shape`. AUDIT #217.

### A mid-stream failure left Responses output items open

**File**: `wiwi/wire/openai_responses.py`.

`_completed()` sweeps every open output item before its terminal event (the
round-25 fix, AUDIT #111), but the `StreamError` arm emitted `response.failed`
immediately. A client that had already received `response.output_item.added`
never got the matching `output_item.done`, leaving the item `in_progress`
forever. The Anthropic encoder closes its blocks on the same path
(`anthropic_messages.py`), so this was an asymmetry between the two surfaces.

The error arm now performs the same sweep before emitting `response.failed`.

**Tests**: `tests/test_fix_round68.py` —
`test_responses_stream_error_closes_open_message_item`,
`test_responses_stream_error_closes_open_tool_item`. AUDIT #218.

### The Anthropic encoder's deferred buffer was unbounded

**File**: `wiwi/wire/anthropic_messages.py`.

Text/thinking arriving while a `tool_use` block is open is buffered in
`_deferred` and flushed when the tool closes (the round-64 fix, AUDIT #156) —
Anthropic content blocks are strictly sequential, so it cannot be emitted
inline. Nothing capped that list, so a stream interleaving unboundedly behind
one long-open tool block grew it without limit: the same unbounded-growth class
that `MAX_TOOL_ARGS_BYTES` (`streaming/validation.py`) and the coalescer's
`max_bytes` already guard.

Added `MAX_DEFERRED_CHARS = 256 * 1024` and a `_defer()` helper that both
append sites route through. On overflow it evicts from the **front** (the newest
content is what the client still needs), trims the boundary entry from its head
so the buffer lands exactly at the cap, and drops any fully-emptied entries so
the flush loop never opens a block with no content. A single delta larger than
the cap is trimmed too, rather than slipping past the guard.

**Tests**: `tests/test_fix_round68.py` —
`test_anthropic_deferred_buffer_is_bounded`,
`test_anthropic_deferred_keeps_the_newest_content_when_capped`,
`test_anthropic_deferred_caps_a_single_oversized_delta`. AUDIT #219.

### Documentation corrected to match the shipped contract

`docs/STREAMING.md` and the `streaming/deltas.py` docstring both claimed a
clean `Finish` on loop detection, "strictly nested per index" tool calls, and
`UsageFinal` exactly once after the last content delta. None matched the code:

- **Loop detection terminates with `StreamError`**, deliberately, and does
  **not** call `_note_stream_failure` so a low-quality model cannot cool a
  healthy deployment or retire a healthy key (AUDIT #108, pinned by
  `tests/test_fix_round43.py`). The docs were the stale side.
- **Parallel tool calls are siblings, not nested.** OpenAI closes every open
  index in a batch at `finish_reason`; Gemini emits Open/Args/Close per
  `functionCall` part. Encoders already assume this; the docs said otherwise.
- **`UsageFinal` arrives more than once** and before the last content delta on
  OpenAI, NIM and Gemini (Gemini attaches `usageMetadata` to every chunk).
  Encoders buffer last-write-wins, so the client still sees one figure.

Also corrected: `ping` is emitted by the **gateway pump**, not
`AnthropicStreamEncoder`; the coalescer's depth threshold is fixed at its
default of 100 with no config knob; and the P0/P1/P2 claim was narrowed —
`PartialJSONParser`/`parse_partial` are test-only (incremental argument
rendering is not shipped) and `StreamTape.replay` has no production caller
(client-facing replay is served by the journal store).

## Round 87 — Zen's transport must declare `forceStream` (2026-09-18)

**File**: `wiwi/providers/opencode_adapter.py`

**Issue**: OpenCode's own provider entry for Zen puts one flag on the
transport that `OpencodeAdapter` never had:

```
transport: { baseUrl: "https://opencode.ai", forceStream: true, ... }
```

Zen answers as an event stream, so the reply a non-streaming caller gets is
SSE. With `force_stream = False` the gateway routed that caller to
`Gateway._call_once`, whose JSON decode cannot read an SSE body — the request
ended as an empty completion or `upstream … returned an undecodable 200
response: JSONDecodeError` (AUDIT #92's wrapper). Cline and WorkBuddy already
declare the flag for the same reason; Zen's declaration was simply missing.

**Before**:
```python
provider_type = "opencode"
force_stream = False
…
def encode_request(self, req, model_id, deployment_params):
    if route == "messages":
        return self._msg.encode_request(req, model_id, …)   # stream: req.stream
```

**After**:
- `force_stream = True` — the transport declaration. `Gateway._call_once` now
  sends every non-streaming caller through `_complete_via_stream`, which pumps
  the SSE and folds the deltas into one `AssistantTurn`; the health healer
  probes this provider with `stream=True` too (`recovery._probe` reads the
  same attribute).
- `encode_request` sets `body["stream"] = True` on the chat, responses and
  messages routes. The declaration alone would have been half a fix: the pump
  asks `build_url` for the streaming URL but encodes the *client's* request,
  so the body would still have said "don't stream" on a connection the gateway
  parses as SSE. Same shape as `cline_adapter`/`workbuddy_adapter`.
- The Gemini route is deliberately excluded from the body force: its wire is
  selected by the URL (`:streamGenerateContent?alt=sse`, from `build_url`), and
  a `stream` key in a `generateContent` body is an unknown field the endpoint
  rejects.

**Live status at the time**: not verifiable — the `io` workspace is unbilled, so
every probe of a free model was refused with `403 FreeTierError` before the
model was reached (#174). Superseded: see round 88 below. `stream: false`
turned out to be one of the three things the free tier rejects, so this flag was
never only an aggregation nicety — for free models it is on the critical path,
which is why it is forced on every route rather than only the responses one.

**Tests**: `tests/test_fix_round87.py` (12) — the declaration, the per-route
body force, the Gemini body/URL split, and two gateway reassembly cases
(free-tier Responses + free chat) with a streaming client as the control. Four
existing suites pinned the pre-fix contract and were moved to the new one:
`test_fix_round27.py::test_encode_chat_delegates_to_openai_shape`
(`stream` is now true), `test_fix_round63.py` (both `union-alpha` cases answer
with the Messages stream), `test_fix_round72.py` (both gateway cases answer
with SSE, and now also assert the aggregated content), and
`test_fix_round73.py::test_gateway_gemini_route_sends_x_goog_api_key` (mocks
`:streamGenerateContent?alt=sse`). AUDIT #266.

## Round 88 — Zen's free-tier 403 is a request-shape gate (AUDIT #267) — 2026-09-19

**File**: `wiwi/providers/opencode_adapter.py` (+ `wiwi.yaml.example`,
`docs/PROVIDERS.md`)

**Reported symptom**:

```
io requires billing (403): OpenCode's free tier can only be used from within OpenCode
```

Round 87's `force_stream` landed the same day and did **not** clear it, because
the flag was only one of the three things the gate checks. The error text reads
like a billing condition, and AUDIT #174 recorded it as one ("free models
require a valid `OPENCODE_API_KEY` *and* a funded workspace", "not recoverable
by any header combination"). It is neither. It means *this request does not look
like the OpenCode client*.

**Why the 2026-09-17 matrix reached the wrong conclusion.** It varied one factor
at a time — bearer, project id, client tag, `x-zen-model`, id format, stream
flag, UA — starting from a baseline that already failed two *other* conditions
(no `bash`/`read` tool payload; a `ses_`+24 hex id where 26 are required). All
three must hold together, so every row 403'd and each axis looked irrelevant,
the stream flag included. Changing one thing while two others are wrong proves
nothing about the one you changed.

**The gate, probed 2026-09-19** (`mimo-v2.5-free`, `POST /zen/v1/chat/completions`,
each row differing from the 200 row in exactly one property):

| property | verdict |
|---|---|
| keyless + stream + `ses_`+12hex+14 + `bash`&`read` | **200 SSE** |
| `stream: false` | 403 FreeTierError |
| no tools / user tools only / `bash` without `read` | 403 FreeTierError |
| `ses_` + 24 hex (right alphabet, two chars short) | 403 FreeTierError |
| `ses_` + 11 hex head + 15 upper | 403 FreeTierError |
| `ses_` + 26 all-hex | 200 SSE |
| missing / wrong `ses_` prefix | 403 FreeTierError |
| `x-opencode-request` of any shape, or absent | 200 SSE |
| canonical session two days old | 200 SSE |
| no `Authorization`, or `Bearer public` | 200 SSE |
| a real account Zen key | **429 FreeUsageLimitError** |
| `User-Agent: opencode/1.16.0` | 426 UpgradeRequired |

So: three request-shape conditions (`stream`, session format, tool payload) plus
the UA version floor; the credential is not part of it, and free quota is
accounted **per session**, which is why a real key only exhausts its own bucket.

**Fix** (all confined to the adapter):

- `is_free_model()` — `-free` suffix plus the stealth `big-pickle` (the live
  catalog lists 9 free models: 8 suffixed + `big-pickle`).
- `canonical_session_id()` / `canonical_request_id()` — the CLI's
  `^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$` shape, with the patterns exported so the
  tests assert against the minter's own constants rather than a re-typed regex.
- `stable_session_id()` — one session per credential (SHA-256 bucket, LRU-capped
  at 1000, TTL 3600 s). Re-minting per request is what produced the 429 storm.
- `_cloak_chat_tools()` / `_cloak_responses_tools()` — inject `bash`+`read` per
  route shape; a client tool with the same name is never replaced, and the
  decoys are only added on free models.
- `_force_auto_tool_choice()` — the two Muse Spark free models reject every
  non-`auto` form with 400, so it collapses on that allowlist only.
- `stream_options: {include_usage: true}` on chat-route requests the client did
  not ask to stream — without it Zen sends no usage frame and every aggregated
  turn prices on the estimator. Probed: Zen accepts the field and returns real
  counts.
- The Gemini route is still excluded from the body force, and the Messages route
  is deliberately **not** cloaked: its only free-tier member was `union-alpha`,
  now retired upstream, so there is no evidence for a tool shape there and an
  OpenAI-format decoy in an Anthropic body would be an invented field.

**Also found**: `union-alpha` is gone from the live catalog and answers
`401 ModelError` on all three routes. Round 63's `_MESSAGES_PREFIXES` entry is
therefore dead config and its tests pass only against a mocked endpoint; the
Messages *route* itself remains live for `claude-*`/`qwen*`. Left in place —
harmless, and removing it is a separate change.

**Tests**: `tests/test_fix_round88.py` (21). `tests/test_fix_round29.py`'s
"fresh session per request" pair pinned the old behaviour and was rewritten to
the reuse contract (shared per credential, isolated across credentials, request
id still per request).

**Verified live end-to-end** through `Gateway.complete` on the non-streaming
path, keyless: `mimo-v2.5-free` → 200 `"ok"` 500/36 tokens, `big-pickle` → 200
`"ok"` 387/3, `muse-spark-1.3-contributor-free` → 200 569/48 — all
`estimated=False`. All three were 403 before this change.

**Open**: #174's entitlement classification is no longer exercised by any real
upstream response (those probes were these same misread 403s). Whether
`FreeTierError` should still map to *billing* is worth revisiting now that wiwi
can avoid it by shape.

## Round 89 — The decoys must not answer as tool calls (AUDIT #268) — 2026-09-19

**File**: `wiwi/providers/opencode_adapter.py`

**Found while verifying round 88 live.** Injecting `bash`/`read` to satisfy the
free tier's tool-payload condition makes those tools callable, and a model asked
to do exactly what one of them is named for will oblige. First live probe of the
`read` case:

```
[chat] "Read the file config.py." -> tool_calls=['read']  (no text at all)
```

The client never declared `read`, cannot execute it, and now sits waiting for a
dispatch — worse than the 403 this whole line of work replaced.

**Why it cannot be fixed on the request side**: the gate requires the tools to be
*offered*, so they cannot be withheld or removed, and a request carrying client
tools keeps `tool_choice` at `auto` (only toolless requests get `"none"`). The
only place the problem can be solved is the response.

**After** (`encode_request` records what it injected; both decode paths act on
it):
- `_cloak_chat_tools` / `_cloak_responses_tools` now **return the names they
  actually added**. A client's own `bash` is left in place by the cloak, so it is
  not in that set and its calls pass through untouched — name alone never
  decides, per-`_decoy_names` does.
- `decode_stream_event` → `_filter_decoys`: drops the
  Open/Args/Close triple for a decoy index (indices tracked in
  `_decoy_indices`, so args arriving in later events go with their open) and
  rewrites `Finish("tool_call")` → `Finish("stop")` when nothing real survived.
- `decode_response` → `_filter_decoys_turn`: the same rule on the aggregated
  path, for both the chat and responses decoders.
- State is cleared at the top of `encode_request`, before the route dispatch, so
  the Gemini early return cannot leave a previous request's filter armed.

Dropping the triple whole is legal at this layer, and only because it *is* this
layer: the wire encoders assign client-visible indices themselves
(`anthropic_messages._tool_blocks`) and drop an `ArgsDelta` whose block never
opened, so a gap in the IR index sequence is invisible to every client dialect.

**Tests**: `tests/test_fix_round89.py` (13) — the cloak's return value (both
routes, and the client-owns-`bash` case), filter state reset between requests,
whole-triple drop with the finish rewrite, a real call surviving beside a decoy,
the client-owned call passing through, a paid request arming nothing, args
arriving in a later event than their open, and the aggregated path through both
`_filter_decoys_turn` and a real `decode_response`. Verified load-bearing:
neutralising the drop logic fails 6 of the 13.

**Verified live**: the exact prompt that produced the phantom call now returns
`finish: stop`, `tool_calls: None`, content *"I'm sorry, but I don't currently
have access to the `read` tool…"* through the gateway's non-streaming path.
Repeating the probe with `tool_choice: "none"` (toolless request) also answers in
text, so the request-side hint is respected when the client sends no tools — but
the response-side filter is what makes the guarantee.

---

## Round 90 — Bidirectional Anthropic ↔ OpenAI translation gets a shared spine (AUDIT #269) — 2026-09-20

**Files**: `wiwi/ir/translation.py` (**new**), `wiwi/core/recovery.py`,
`wiwi/providers/openai_adapter.py`, `wiwi/providers/anthropic_adapter.py`,
`wiwi/wire/openai_chat.py`, `wiwi/wire/anthropic_messages.py`

**Scope**: this round deliberately adds *no* new endpoint and *no* new provider
type. Both directions already existed — Anthropic surface → OpenAI provider
(`test_integration.py::test_anthropic_surface_to_openai_backend`) and OpenAI
surface → Anthropic provider. What was missing was a **single spine** underneath
them: the finish-reason vocabularies were maintained as independent dict
literals in four modules, with nothing checking that any two agreed. This round
extracts the shared maps, makes the two Anthropic maps prove they are inverses
of each other, and hardens the two seams where a value crosses a dialect
boundary without a total mapping.

### 90.1 The finish-reason maps live in one place

**File**: `wiwi/ir/translation.py` (new), `wiwi/providers/openai_adapter.py`,
`wiwi/wire/openai_chat.py`

`wiwi/ir/` is the one package that may hold a helper used by both `wire/` and
`providers/` — anything in either of those two cannot be imported across the
seam (CLAUDE.md "Import Rules"). The new module therefore imports only
`wiwi.ir.types` and carries no dialect *logic*: the OpenAI-vocabulary map is
data, and the Anthropic maps stay where they are.

**Before** — the same four-entry dictionary written out inline, three times in
`wire/openai_chat.py` alone (the module-level response encoder, the stream
encoder's `final_frame`, and `decode_response`) plus twice in
`openai_adapter.py`:

```python
{"stop": "stop", "length": "length", "tool_call": "tool_calls",
 "content_filter": "content_filter"}.get(stop, "stop")
```

**After**:

```python
from wiwi.ir import translation as tr
...
fr = tr.ir_to_openai_finish(turn.stop_reason)   # and .normalize_finish_reason inbound
```

The inbound direction is deliberately **wider** than the outbound one. Upstreams
spell a tool-call stop as `tool_calls`, `tool_use`, or `function_call`
depending on the vendor, and a length stop as `length` or `max_tokens`;
`normalize_finish_reason` accepts all of them, while `ir_to_openai_finish`
emits only the canonical OpenAI spelling. An unknown reason is now logged
(`finish_reason_unmapped`, once per distinct value) instead of being silently
folded to `"stop"` — the fold is still the behaviour, but it is no longer
invisible.

### 90.2 The two Anthropic stop-reason maps are proven inverses

**Files**: `wiwi/providers/anthropic_adapter.py`, `wiwi/wire/anthropic_messages.py`

`_STOP_REASON_IN` (provider → IR) and `_STOP_REASON_OUT` (IR → client) each had
a comment claiming the relationship. A comment is not a check, and a reason that
survives the outbound trip and comes back as a *different* one is invisible in
every single-direction test. Both maps are now documented as inverses of each
other by name and anchored by
`test_translation_enhancements.py::test_anthropic_stop_reason_maps_are_inverses`,
which fails on a one-sided edit (AUDIT #156 is the historical instance of this
class: `pause_turn` and `model_context_window_exceeded` were both folded to
`stop`, which ended server-tool turns early and hid context overflow from
auto-compact). No behavioural change here — the maps were already correct.

### 90.3 A redacted-thinking block must not be replayed as OpenAI content

**File**: `wiwi/providers/openai_adapter.py`

`ir.ThinkingPart` carries both ordinary thinking and Anthropic's
`redacted_thinking` blob. An Anthropic provider answers the OpenAI surface with
the latter, and the only thing keeping the opaque blob out of an OpenAI-shaped
upstream was that a redacted block's `text` happens to be empty — an invariant
of the *other* module, not of this one.

**After** (the `ThinkingPart` arm of the encoder):

```python
elif isinstance(p, ir.ThinkingPart):
    if p.block_type == "redacted_thinking":
        # ... the payload must never reach an OpenAI-shaped upstream as
        # content, so the block is dropped explicitly rather than by
        # relying on that emptiness (AUDIT #103).
        continue
    reasoning += p.text
```

Defensive, not load-bearing — `p.text` is always `""` for a redacted block
today, so the observable behaviour is unchanged. It stops being harmless the
moment the blob is parked in `text` instead of `data`, which is why the drop is
explicit.

### 90.4 Dropped inbound params are observable

**File**: `wiwi/wire/anthropic_messages.py`

The Anthropic codec carries extras by **allowlist** (`_PASSTHROUGH_KEYS`) while
the OpenAI codec uses a **denylist** (`_KNOWN_KEYS`) — the asymmetry is
deliberate (Anthropic's parameter surface is small and its block schema is not
forward-compatible; OpenAI's is large and additive), but it means a new
Anthropic parameter that is neither modelled in the IR nor listed for
passthrough vanishes with no trace at all. `_note_unmodelled_params` now logs
`anthropic_params_unmodelled` with the dropped keys, once per distinct key. The
drop itself is unchanged.

### 90.5 A 200 carrying an Anthropic/OpenAI error body was called HEALTHY (AUDIT #269)

**File**: `wiwi/core/recovery.py`

`_body_is_error_envelope` recognized only WorkBuddy's
`{"code": <non-zero>, "msg": …}` envelope, so an Anthropic- or OpenAI-shaped
upstream that answered HTTP 200 with its own dialect's error object was declared
HEALTHY. The healer's job is to *restore* a credential on a successful probe, so
it re-armed the very failure it exists to clear. Those bodies are invisible one
layer down: `decode_response(200, <error envelope>)` returns an empty but
*successful* `AssistantTurn` with no exception and no signal.

The fix gains the Anthropic (`{"type": "error", "error": {...}}`) and OpenAI
(`{"error": {"message"|"type"|"code"|"param"}}`) shapes by their own structural
markers — no `provider_type == …` branch, so no dialect branching enters
`core/`. An adapter-owned veto hook was considered and rejected: `probe_verdict`
is a pure synchronous classifier with no adapter in scope, and adding a sixth
method to the five-method `ProviderAdapter` Protocol for a consumer that does
not exist is unwarranted.

`_probe_request(stream, model_id)` was also changed to take the deployment's
model id rather than a placeholder. **That half turned out to be a no-op on
observable behaviour** and the claim originally made for it is retracted below;
the signature change was kept only because it makes the function and its call
site agree about who owns the model name.

**Tests**: `tests/test_fix_round91.py` (17). Verified load-bearing: reverting the
envelope widening fails all four error-body cases.

> **Retraction, caught in review of this round.** This entry first asserted that
> the placeholder `model="wiwi-health-probe"` "never reached the wire" only
> *because* the call site passed `dep.model_id`, and that the two halves
> "disagreed". Both statements are false, and the tests that "proved" them could
> not have detected the claim. No adapter reads `ir.Request.model` for the wire
> body — every one sets it from `encode_request`'s own `model_id` argument
> (`openai_adapter.py:233`, `anthropic_adapter.py:439`,
> `opencode_adapter.py:845`, the rest via `super()`), and the pre-fix call site
> already passed `dep.model_id` as that argument
> (`git show HEAD:wiwi/core/recovery.py:492`). Encoding the probe request through
> six provider types with the OLD and NEW `_probe_request` produces
> **byte-identical wire bodies**, so the placeholder sat in a field nothing
> reads: a code-clarity defect, not a misclassification. The nine-way
> parametrized test passed `"real-model-id"` as *both* arguments, so it held even
> when `_probe_request` discarded its argument entirely, and it failed against
> pre-fix source only via `TypeError` — a signature mismatch misread as a
> behavioural catch. The test now passes a *placeholder* to `_probe_request` and
> a *different* id to `encode_request` and asserts the wire carries the latter,
> which does fail when the two disagree (verified by making an adapter prefer
> `req.model`: 8 of 9 cases fail).

**Cross-module tests**: `tests/test_translation_helpers.py` (13) for the shared
helpers, `tests/test_translation_enhancements.py` (+16) for the four codec and
adapter changes above, `tests/test_integration.py` (+8) for the reverse
direction end-to-end (OpenAI Chat surface on an Anthropic deployment:
non-streaming, the wire body the provider actually received, a tool call, a
streaming stream with `reasoning_content`, a streamed tool call reassembled from
`input_json_delta` fragments, an Anthropic 429 reaching an OpenAI client as
`{"error": {...}}` with the status preserved, a mid-stream error frame, and three
`hypothesis` invariants asserting on the IR rather than on bytes).

### 90.6 Whole-branch review: three gaps the shared spine exposed or left open

Recorded here because two of them are consequences of *this* round's change, and
the next agent touching `ir_to_openai_finish` / `normalize_finish_reason` needs
to know they exist before assuming the spine is closed. All three are
`AUDIT.md` entries (#270, #271, #272) and none is fixed in this round — each sits
in a file outside the round's approved scope.

**#271 — the OpenAI surface's streaming encoder emits `tool_calls` with no tool
call.** `wiwi/wire/openai_chat.py:429-437` applies the A1 downgrade guard only
when a builtin was suppressed, while the non-streaming encoder on the same
surface and the Anthropic encoder both guard unconditionally. **This round made
it reachable:** `normalize_finish_reason` newly accepts the Anthropic spelling
`tool_use` → IR `tool_call`, which the pre-round-90 inline closures did not, so
an OpenAI-shaped upstream that reports `tool_use` with zero tool calls now yields
`finish_reason: "tool_calls"` on a chunk with no `tool_calls` array:

```
upstream finish_reason='tool_use', zero tool calls
  IR deltas : ['TextDelta', 'Finish']
  IR finish : ['tool_call']
  CLIENT    : finish_reason = 'tool_calls'
```

The behaviour *was* strictly worse before on the outbound side (#90.1 fixed IR
`tool_call` mis-spelling as `stop`); the correct fix is to drop the
`_suppressed_builtin` conjunct so both paths guard the same way.

**#272 — the Responses surface reports `tool_call` with no tool item as
`completed`.** `openai_responses.py:394` `_INCOMPLETE_REASONS` covers only
`length` and `content_filter`, so `tool_call`, `pause_turn`, `stop_sequence`,
`context_window_exceeded`, and `compaction` all encode as a successful
completion. `context_window_exceeded` is the notable one: the IR carries that
distinct value specifically because collapsing an overflow into `stop` hid it
from auto-compact (AUDIT #156), and this map re-collapses it on the Responses
side. Not a round-90 regression (the old closures never produced those IR values
from an OpenAI-shaped upstream either), but the round widened the vocabulary that
can reach it.

**#270 — three adapters keep their own narrower finish-reason map** (`nim`,
`openrouter` ×2). Pre-existing and unchanged; listed here so the residual set is
in one place. A NIM/OpenRouter deployment spelling a tool stop `tool_use` still
reaches the client as `stop`.

### 90.7 The SSE arm of the healer fix (found in the same review)

`_body_is_error_envelope`'s force-stream arm is what carries the #269 fix on
`cline`/`workbuddy`/`opencode`, all of which set `force_stream` and therefore get
SSE even on HTTP 200. Verified against the verbatim pre-fix source
(`git show 365dd2c:wiwi/core/recovery.py`) that this arm *is* covered by the
change — it is written inside `try/except (ValueError, TypeError)` and
unconditionally, not `is_error_object`-guarded:

```
b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",…}}\n\n'
  pre-fix  -> False (HEALTHY)      post-fix -> True (UNREACHABLE)
```

One layer down the same bytes decode to an empty list with no exception — the
invisible-success shape the whole entry is about. Two residual items are recorded
in AUDIT #269 rather than fixed: neither arm bounds the size of the body it
`json.loads`'s (a byte cap on a 1-token probe response would be cheap hardening),
and this fixes only the *healer's* classification — a 200-carrying-an-error-body
still reaches a normal request's client as an empty successful turn, which is a
separate question and is not claimed here.

---

# Round 92 — the request-log cap, batch durability, and percentile merging (2026-09-22)

Four defects in `wiwi/logging_core/`, all in the request-logging path. Each was
reproduced before the fix and re-measured after; all four are behavioural, not
cosmetic.

## 92.1 The row cap silently stopped trimming under a `ts` tie

**File**: `wiwi/logging_core/db_sink.py` — `enforce_log_cap`, `rollup_and_prune`

**Issue**: `enforce_log_cap` chose its cutoff row with
`ORDER BY ts DESC, id DESC LIMIT 1 OFFSET :max_rows-1` — an id-tiebroken
selection — then passed only the raw `ts` to `rollup_and_prune`, whose
`SELECT`/`DELETE` matched `ts < :cutoff` alone. The id tiebreak was dropped
between choosing the boundary and enforcing it. When many rows shared the
boundary `ts` (a saturated `_pump` drain of up to 200 events, a coarse clock, a
bulk insert), nothing sorted *strictly* below the boundary matched `ts <`, so the
cap deleted almost nothing — and it did so **silently**: the sweep still logged
`request_logs_pruned`, and `request_logs` sat over `log_max_rows` indefinitely.
The in-code comment ("ties on ts keep a few extra rows … the next sweep trims
them") described the benign case; the dense-tie case kept *all* of them, forever.

Measured on 100 rows sharing one `ts`, cap 50: **deleted 0, 90 remaining** (a
partial tie of 90+10 deleted only the 10 strictly-older rows).

**After**: `rollup_and_prune` takes an optional `cutoff_id`. The doomed set is
built once and applied identically to the rollup `SELECT` and the `DELETE`:

```sql
ts < :cutoff OR (ts = :cutoff AND id < :cut_id)
```

The age path passes no id and is unchanged. Same 100 rows, cap 50 → **deleted
50, 50 remaining**; exactness verified (the boundary row is KEPT, the newest N
survive by id).

**Tests**: `tests/test_fix_db_cap_and_batch.py` (same-`ts`, all-same-`ts`,
partial-tie, re-enforcement after a blocked sweep, age-path unchanged) and
`tests/test_fix_db_cap_and_batch_e2e.py::test_e2e_ts_tie_cap_bounds_table_and_overview_survives`.

## 92.2 One malformed row discarded its entire batch

**File**: `wiwi/logging_core/db_sink.py` — `write_requests`

**Issue**: a drain was a single multi-row `INSERT` in one transaction, so a row
a strict backend rejects (Postgres enforces typed columns where SQLite coerces)
rolled back **every sibling** — up to 200 good rows. Measured: 199 good + 1
`NOT NULL`-violating row persisted **0 rows**.

**After**: on failure the batch is retried row-at-a-time, dropping only the
offending row. An all-bad batch still re-raises, so the caller's
`failed_request_log_writes` accounting continues to reflect a real loss (a
DB-unavailable outage must not be mistaken for bad rows). The per-row fallback
logs a terse `request_log_row_dropped` (error type + request id, no traceback —
a full traceback per row turns one bad drain into megabytes of log). Measured:
same batch now persists **199**.

**Tests**: `tests/test_fix_db_cap_and_batch.py` (mixed batch, all-good fast path
untouched, all-bad still raises, empty batch is a no-op) and
`tests/test_fix_db_cap_and_batch_e2e.py::test_e2e_bad_row_keeps_siblings_visible_in_the_api`.

## 92.3 Mixed-window percentiles were wrong by up to 45%

**File**: `wiwi/logging_core/hist.py` (new), `wiwi/logging_core/db_sink.py`

**Issue**: `request_rollups` stored **one p95 float per bucket** (`tps_p95`,
`ttft_p95_ms`, `latency_p95_ms`) and the reader merged it with raw samples by a
sample-count-weighted mean. A mean of quantiles is not the quantile of the
union, and the docstring claimed the opposite ("weighting each side's p95 … and
taking the larger") — the code and its own comment disagreed. Measured on a
window half fast and half slow: **reported 547 where the true p95 was 1000**.

**After**: each bucket stores a compact **log-scale histogram** per metric
(10 bins/e-fold, geometric midpoints) in a new `p95_hist` TEXT column — one JSON
object for all series, so the schema grew by a single column, not one per
metric. Merging is "add bin counts, then take the percentile", which is
correct rather than approximately-correct:

- an all-raw window is **exact** (raw samples keep their exact values; only the
  rolled-up counts are located at their bin midpoints);
- an all-rolled-up or mixed window is within the bin resolution — measured
  **3.4%** on the same shape that was 45% off before.

Counts merge across sweeps, so a bucket written by an earlier sweep and one
written now combine instead of the later overwriting the earlier. Rows written
before the column existed (blank `p95_hist`) fall back to the legacy scalar
columns; a garbled value degrades to the same fallback. Verified live through
the app: 300 rows (150 rolled-up slow + 150 raw fast) → p95 1043.1 vs a true
1083.5, totals preserved.

**Honest bound**: the residual ~3% is inherent — removing it entirely needs
every sample kept forever, which is the unbounded growth the rollup exists to
prevent. `10 bins/decade` is the measured optimum; `20/decade` degrades (float
precision in the bin search), verified rather than assumed.

**Tests**: `tests/test_hist_percentiles.py` (13).

## 92.4 Adding a percentile metric was easy to get wrong

**File**: `wiwi/logging_core/db_sink.py`, `tests/test_hist_percentiles.py`

**Issue**: the metric set lived implicitly across `_HIST_SERIES`, the legacy
column names, both DDLs and the migration list. The old note in `REMINDERS.md`
("if a new percentile metric is added, it needs a column in `request_rollups`
and a pass in `rollup_and_prune`") was the whole safeguard.

**After**: `_HIST_SERIES` is the single source of truth — the writer, the
histogram merge and the overview reader all iterate it, and the legacy scalars
are built from it with `zip(..., strict=True)` so the two cannot disagree.
`_LEGACY_P95_COLS` names the scalar columns. Two tests fail loudly if the DDL,
the migration list or the tuples drift:
`test_rollup_metric_lists_stay_in_sync` and
`test_both_ddls_and_migration_carry_every_rollup_column` (both verified to fail
when deliberately broken).

**Whole-branch**: 2598 tests pass, ruff clean; the 4 pre-existing DB suites
(`test_stats_db`, `round56`, `round57`, `round59`, `round65`, `round85`) are
unchanged and green, which is the evidence the histogram did not alter any
reported total.
