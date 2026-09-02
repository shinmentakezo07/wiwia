# UPDATE.md — Translation Layer Fixes & OpenRouter Adapter

> **Session date**: 2026-08-23
> **Status**: All changes verified — 213 tests pass, ruff clean
> **Scope**: OpenAI ↔ Anthropic cross-provider translation fixes, OpenRouter dedicated adapter, multi-turn conversation bug fix

This document is the changelog for all translation-layer work done in this session. Future agents encountering issues in these areas should read this first for quick context.

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
