# Legacy Completions Surface (`POST /v1/completions`)

**Date:** 2026-10-01
**Status:** Accepted — user approved 2026-10-01
**Sub-project:** A of five (A: `/v1/completions` · B: stateful Responses · C: OpenTelemetry ·
D: router shedding/lanes/affinity · E: Realtime WebSocket proxy)

## Goal

Serve OpenAI's legacy text-completion wire shape — `POST /v1/completions` with a
`prompt` (string or string array) and a `choices[].text` response — through the
same hub-and-spoke pipeline every other surface uses, by adding exactly one wire
codec module plus one route. Tracked as the missing surface in `AUDIT.md` #213.

## Why (context)

`MVP.md` F1 explicitly records `/v1/completions` as **not implemented** — no route
was ever registered. It is the last gap on the "clients that speak a dialect we
claim to support" axis: the repo's design rule is *adding an inbound surface = one
module in `wiwi/wire/`*, and the three existing surfaces each cost exactly that.
Completions is a strictly simpler shape than chat (no messages, no roles, no tool
protocol), so it exercises that rule rather than straining it.

## Current state

- `wiwi/wire/` holds `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py`.
  Each exposes `decode_request`, `encode_response`, a stream encoder class,
  `error_body`, and its dialect's exception type.
- `wiwi/server/app.py:2123-2190` declares one route per surface; each is a three-line
  wrapper over `run_chat_like(request, surface, body, decode, encode)`
  (`app.py:1506`). The pipeline (auth, rate limit, budget reservation, response
  cache, stream journal, gateway dispatch, logging, billing) is shared and is not
  touched by this change.
- `Surface = Literal["chat", "responses", "messages"]` (`wiwi/core/context.py:13`).
  It is consumed only by: error-envelope selection (`app.py:1454-1490`), the stream
  encoder dispatch (`app.py:1888-1896`), the Anthropic ping gate
  (`core/gateway.py:941`), and the `LogEvent.surface` field (`gateway.py:2184`,
  stored verbatim in `request_logs.surface`).
- `GenParams` (`wiwi/ir/types.py:203`) already carries everything a completion needs:
  `temperature`, `top_p`, `max_tokens`, `stop`, `seed`, `n`.

## Design

### 1. Codec — `wiwi/wire/openai_completions.py` (new)

Same module contract as its three siblings, so nothing in `core/`, `router/`,
`auth/`, `streaming/` or `logging_core/` learns a new concept.

```python
class DialectError(ValueError): ...          # imported from openai_chat, as the
                                             # other codecs do

def decode_request(body: dict[str, Any]) -> ir.Request
def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]
class CompletionStreamEncoder:               # same shape as ChatStreamEncoder:
    def __init__(self, model: str, req_id: str, include_usage: bool = False)
    def feed(self, d: dl.IRStreamDelta) -> bytes | None
    def final_frame(self, usage=None, stop=None) -> bytes
def error_body(status: int, etype: str, message: str) -> dict[str, Any]
```

**Decode.** `prompt` is a `str` **or** `list[str]` (the wire allows both). Both map
to IR messages:

- `str` → one `Message(role="user", parts=[TextPart(prompt)])`.
- `list[str]` → no IR representation. **One entry** is accepted (lenient, maps to a
  single user message). **More than one entry is rejected** with a `DialectError`
  naming the limitation — a silently-truncated prompt is a wrong answer, and
  OpenAI's own multi-prompt form is legacy.
- `n`, `stop`, `temperature`, `top_p`, `max_tokens`/`max_completion_tokens`
  (via `openai_chat._max_token_cap`), `seed`, `stream`,
  `stream_options.include_usage` map to `GenParams`/`Request` exactly as the chat
  codec maps them.
- `suffix` is appended to the user message text (it is an infill decoration, not a
  separate turn); `echo: true` is honoured by prepending the rendered prompt to the
  encoded `choices[].text`.
- `logprobs`, `best_of > 1`, `prompt` as a token-id array (`list[int]`), and
  `list[str]` of length > 1 are **rejected** (400, `invalid_request_error`, message
  naming the offending parameter). They have no IR representation; silently
  ignoring a parameter the caller sent is precisely the failure class `UPDATE.md`
  exists to record.
- Tools are absent from this dialect: `tools`/`tool_choice` in the body are ignored
  (they are not part of the wire shape).

**Encode (non-streaming).**

```json
{"id": "cmpl-<req_id>", "object": "text_completion", "created": <unix>,
 "model": "<model>",
 "choices": [{"index": 0, "text": "<text>", "logprobs": null, "finish_reason": "<mapped>"}],
 "usage": {"prompt_tokens": …, "completion_tokens": …, "total_tokens": …}}
```

`finish_reason` goes through `wiwi.ir.translation.ir_to_openai_finish`. Tool calls
returned by the model are **dropped with a translation warning** recorded on
`ctx` (via the existing `_flag`/`_record_translation_warnings` path in
`core/gateway.py`): a completions client cannot run a tool loop, so a
`finish_reason: "tool_calls"` with no body is worse than `"stop"`. The finish
reason is therefore forced to `"stop"` whenever tool calls were suppressed.

**Encode (streaming).** SSE frames with `object: "text_completion"`:

- first frame: `{"choices":[{"index":0,"text":"",…}]}` (role-less opening, as the
  real API sends);
- `TextDelta` → `{"choices":[{"index":0,"text":"<fragment>"}]}`;
- `ThinkingDelta` → **dropped** (this dialect has no reasoning field);
- `ToolCall*` → dropped, same reason as above; sets a suppression flag so the
  finish reason becomes `"stop"`;
- `UsageFinal` → held for the final frame;
- `Finish` → `finish_reason`;
- terminal `final_frame()` emits the finish frame, then — when
  `stream_options.include_usage` was set — a usage-only frame, then `data: [DONE]`
  (emitted by the shared `_stream_response` for `style == "chat"`).

The `style` returned by `_encoder_for` for this surface is `"chat"`, which is what
makes the shared terminal-frame and `[DONE]` sequencing work unchanged.

### 2. Surface plumbing

- `wiwi/core/context.py`: `Surface` gains `"completions"`.
- `app.py:_encoder_for`: `if surface == "completions": return
  CompletionStreamEncoder(model, req_id, include_usage=include_usage), "chat"`.
- `app.py:_error_body_for`: completions falls through to `oc.error_body` — the
  dialect's error envelope is the OpenAI one, so **no new branch is needed**; the
  function's existing default already returns it.
- `app.py:_surface_for_path`: `if path.startswith("/v1/completions"): return
  "completions"` — a malformed body must be reported in this dialect before the
  codec has run (and `_err`'s `surface == "messages"` check must not match).
- Route, next to the other three:

```python
@app.post("/v1/completions")
async def completions(request: Request):
    body, jerr = await json_body(request)
    if jerr:
        return jerr
    return await run_chat_like(request, "completions", body,
                               ocmpl.decode_request, ocmpl.encode_response)
```

### 3. What deliberately does **not** change

- `gateway.py:941` (`ctx.surface == "messages"`) — completions is not the Anthropic
  dialect and must not receive Anthropic ping frames.
- Response cache, stream journal, budget reservation, rate limiting — all keyed off
  data the codec produces, so they work unmodified.
- The admin SPA: `surface` is a log dimension, already rendered as a free-text
  value; no `web/` change is required by this sub-project.

## Testing

1. **Codec unit tests** (in `tests/test_codecs.py`, beside the other three codecs):
   string prompt, single-element array prompt, multi-element array rejected, each
   refused parameter rejected, non-streaming encode shape, streaming frame sequence
   (text fragments → finish → `[DONE]`), usage frame only when requested, tool-call
   suppression.
2. **Regression file** `tests/test_fix_round113.py` (next unused number — verify with
   `ls tests/test_fix_round*.py`): end-to-end through `create_app` +
   `httpx.ASGITransport` + `LifespanManager` with a `respx`-mocked upstream, both
   streaming and non-streaming; assert the response `object`, the usage block, the
   `request_logs` surface value, and that a 400 refusal uses the OpenAI error
   envelope.
3. **Live smoke:** boot the server and `curl` `/v1/completions` (streaming and not)
   against a configured provider, observing real frames. Recorded in the commit.

## Non-goals

- `echo: true` beyond the simple prepend, `logprobs`/`best_of` emulation.
- `/v1/embeddings` (separate endpoint, separate decision).
- Any change to the admin UI.

## Risks

- **Wrong `finish_reason` for tool-using backends.** Mitigated by the suppression
  flag above (`"stop"`, with a translation warning).
- **Multi-prompt clients.** Rejected loudly rather than answered wrongly; if a real
  client needs it, the IR can gain a `n>1` path later.
