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

## Files Changed Summary

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
| `wiwi.yaml` | Provider type changed to `openrouter` |
| `wiwi.yaml.example` | OpenRouter example added |
| `tests/test_translation_enhancements.py` | **NEW** — 39 tests covering all Round 1 + 3 fixes |
| `tests/test_openrouter_adapter.py` | **NEW** — 22 tests covering Round 2 + multi-turn |

**Total: 213 tests pass, ruff clean.**
