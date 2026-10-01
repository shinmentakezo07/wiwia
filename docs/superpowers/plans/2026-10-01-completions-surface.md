# Legacy Completions Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking.

**Goal:** Serve `POST /v1/completions` (legacy OpenAI text completion) through the existing
`run_chat_like` pipeline via one new wire codec module.

**Architecture:** One codec module (`wiwi/wire/openai_completions.py`) exposing the same four
symbols as its three siblings, one route in `create_app`, one added literal in `Surface`, one
branch in `_encoder_for`. No change to `core/`, `router/`, `auth/`, `streaming/`,
`logging_core/`.

**Tech Stack:** Python 3.12, existing IR (`wiwi/ir/types.py`), existing delta taxonomy
(`wiwi/streaming/deltas.py`), pytest + respx + asgi-lifespan.

**Spec:** `docs/superpowers/specs/2026-10-01-completions-surface-design.md`

## Global Constraints

- `requires-python = ">=3.11"`; ruff `line-length = 100`, `target-version = "py311"`.
- Frozen dataclasses for IR/streaming hot-path types; adapters mutate only their own state.
- Library code uses `structlog`, never `print`.
- Import from the module that owns the symbol; never add a re-export layer.
- Gate before claiming done: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`.
- Never commit `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `*.har`,
  `.wiwi/`, `.verify/`.
- Tests: bare `async def test_…` (no decorator), no `conftest.py`, each file builds its own
  fixtures; respx in **decorator** form.

---

### Task 1: Decode `prompt` into IR

**Files:**
- Create: `wiwi/wire/openai_completions.py`
- Test: `tests/test_codecs.py` (append)

**Interfaces:**
- Consumes: `wiwi.ir.types` (`Request`, `Message`, `TextPart`, `GenParams`, `coerce_int`),
  `wiwi.ir.translation.carry_extras`, `wiwi.wire.openai_chat.DialectError`,
  `wiwi.wire.openai_chat._max_token_cap`, `wiwi.wire.openai_chat._stop_list`.
- Produces: `decode_request(body: dict[str, Any]) -> ir.Request`, `DialectError` re-export,
  module constant `_KNOWN_KEYS: set[str]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_codecs.py`)

```python
def test_completions_string_prompt_decodes_to_one_user_message():
    req = ocmpl.decode_request({
        "model": "gpt-3.5-turbo-instruct",
        "prompt": "say hi",
        "max_tokens": 5,
        "temperature": 0.2,
    })
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"
    assert req.messages[0].parts[0].text == "say hi"
    assert req.gen_params.max_tokens == 5
    assert req.gen_params.temperature == 0.2
    assert req.stream is False


def test_completions_single_element_prompt_array_is_accepted():
    req = ocmpl.decode_request({"model": "m", "prompt": ["only"]})
    assert req.messages[0].parts[0].text == "only"


def test_completions_multi_prompt_array_is_rejected():
    with pytest.raises(ocmpl.DialectError):
        ocmpl.decode_request({"model": "m", "prompt": ["a", "b"]})


def test_completions_token_id_prompt_is_rejected():
    with pytest.raises(ocmpl.DialectError):
        ocmpl.decode_request({"model": "m", "prompt": [1, 2, 3]})


def test_completions_unsupported_params_are_rejected():
    for key, value in (("logprobs", 3), ("best_of", 2), ("echo", True)):
        with pytest.raises(ocmpl.DialectError):
            ocmpl.decode_request({"model": "m", "prompt": "x", key: value})


def test_completions_missing_model_is_rejected():
    with pytest.raises(ocmpl.DialectError):
        ocmpl.decode_request({"prompt": "x"})


def test_completions_suffix_is_appended_to_the_prompt():
    req = ocmpl.decode_request({"model": "m", "prompt": "def f():", "suffix": "return 1"})
    assert req.messages[0].parts[0].text == "def f():return 1"
```

Add at the top of the file, beside the existing imports:

```python
import pytest

from wiwi.wire import openai_completions as ocmpl
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_codecs.py -q -k completions`
Expected: FAIL — `ModuleNotFoundError: No module named 'wiwi.wire.openai_completions'`.

- [ ] **Step 3: Write the module**

Create `wiwi/wire/openai_completions.py`:

```python
"""OpenAI legacy Completions wire codec: ``POST /v1/completions``.

The oldest OpenAI wire shape: a bare ``prompt`` string and ``choices[].text``
instead of a chat transcript. No roles, no tool protocol — so the codec maps the
prompt onto a single user message and lets the shared pipeline do everything else
(auth, routing, retries, budget, logs, billing).

Refusals are deliberate and loud: ``logprobs``, ``best_of > 1``, ``echo`` and a
token-id ``prompt`` have no IR representation, and quietly dropping a parameter the
caller sent is the failure class UPDATE.md exists to record.
"""

from __future__ import annotations

import time
from typing import Any

import orjson

from wiwi.core.context import RequestContext
from wiwi.ir import translation as tr
from wiwi.ir import types as ir
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import sse_frame
from wiwi.wire.openai_chat import (
    DialectError,
    _max_token_cap,
    _stop_list,
    _str_or_empty,
)

_KNOWN_KEYS = {"model", "prompt", "suffix", "max_tokens", "max_completion_tokens",
               "temperature", "top_p", "stop", "seed", "n", "stream", "stream_options"}


def _prompt_text(body: dict[str, Any]) -> str:
    """Resolve the wire ``prompt`` to the text of one user turn.

    The wire allows a string, a list of strings (batch), or a list of token ids.
    Only the first two map onto the IR; a batch of more than one and a token-id
    array are refused rather than answered for the wrong prompt.
    """
    prompt = body.get("prompt")
    if prompt is None:
        raise DialectError("'prompt' is required")
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        if not prompt:
            return ""
        if not all(isinstance(p, str) for p in prompt):
            raise DialectError(
                "token-id prompts are not supported; send 'prompt' as a string")
        if len(prompt) > 1:
            raise DialectError(
                "'prompt' may hold at most one string (multi-prompt is unsupported)")
        return prompt[0]
    raise DialectError("'prompt' must be a string")


def decode_request(body: dict[str, Any]) -> ir.Request:
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise DialectError("'model' is required")
    if body.get("n") not in (None, 1):
        raise DialectError("'n' must be 1 (multiple choices unsupported)")
    for key in ("logprobs", "echo"):
        if body.get(key) is not None:
            raise DialectError(f"'{key}' is not supported on this surface")
    best_of = ir.coerce_int(body.get("best_of"))
    if best_of is not None and best_of > 1:
        raise DialectError("'best_of' must be 1 (unsupported on this surface)")

    text = _prompt_text(body)
    suffix = body.get("suffix")
    if isinstance(suffix, str) and suffix:
        # ``suffix`` is code infill: the model continues the prompt *before*
        # this text. The IR has one text channel, so concatenate — the upstream
        # sees the same bytes either way.
        text = f"{text}{suffix}"

    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=_max_token_cap(body),
        stop=_stop_list(body.get("stop")),
        seed=ir.coerce_int(body.get("seed")),
    )
    stream_opts = body.get("stream_options")
    if not isinstance(stream_opts, dict):
        stream_opts = {}
    return ir.Request(
        model=model,
        messages=[ir.Message(role="user", parts=[ir.TextPart(text)])],
        gen_params=g,
        stream=bool(body.get("stream")),
        stream_options_include_usage=bool(stream_opts.get("include_usage", False)),
        extras=tr.carry_extras(body, _KNOWN_KEYS),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_codecs.py -q -k completions`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add wiwi/wire/openai_completions.py tests/test_codecs.py
git commit -m "Decode the legacy completions prompt into IR messages"
```

---

### Task 2: Non-streaming encoder

**Files:**
- Modify: `wiwi/wire/openai_completions.py`
- Test: `tests/test_codecs.py` (append)

**Interfaces:**
- Consumes: `wiwi.ir.types.AssistantTurn` (`text`, `tool_calls`, `stop_reason`, `usage`),
  `wiwi.ir.translation.ir_to_openai_finish`, `wiwi.core.context.RequestContext`.
- Produces: `encode_response(ctx, turn, model, req_id) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_completions_encode_response_shape():
    from wiwi.core.context import RequestContext
    from wiwi.ir import types as ir
    turn = ir.AssistantTurn(text="hello", stop_reason="stop",
                            usage=ir.Usage(prompt_tokens=3, completion_tokens=2))
    out = ocmpl.encode_response(RequestContext(surface="completions", ir_req=_req()),
                                turn, "gpt-3.5-turbo-instruct", "abc123")
    assert out["id"] == "cmpl-abc123"
    assert out["object"] == "text_completion"
    assert out["choices"] == [{"index": 0, "text": "hello", "logprobs": None,
                              "finish_reason": "stop"}]
    assert out["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_completions_encode_response_never_reports_tool_calls():
    from wiwi.core.context import RequestContext
    from wiwi.ir import types as ir
    turn = ir.AssistantTurn(text="", stop_reason="tool_calls",
                            tool_calls=[ir.ToolUsePart(id="c1", name="f")])
    ctx = RequestContext(surface="completions", ir_req=_req())
    out = ocmpl.encode_response(ctx, turn, "m", "r1")
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["choices"][0]["text"] == ""
```

with a module-level helper in the test file:

```python
def _req():
    from wiwi.ir import types as ir
    return ir.Request(model="m", messages=[ir.Message(role="user",
                                                      parts=[ir.TextPart("x")])])
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_codecs.py -q -k "completions and encode"`
Expected: FAIL — `AttributeError: module … has no attribute 'encode_response'`.

- [ ] **Step 3: Implement**

Append to `wiwi/wire/openai_completions.py`:

```python
def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]:
    """Non-streaming body: ``choices[].text`` plus usage.

    Tool calls are dropped and the finish reason forced to ``"stop"``: this
    dialect has no tool protocol, so a ``tool_calls`` finish with no call body
    would be a turn the client can neither run nor complete (the ANALOG of
    AUDIT #271 on the chat surface).
    """
    finish = tr.ir_to_openai_finish(turn.stop_reason)
    if turn.tool_calls or finish == "tool_calls":
        finish = "stop"
    u = turn.usage
    return {
        "id": f"cmpl-{req_id}", "object": "text_completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "text": turn.text, "logprobs": None,
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": u.prompt_tokens,
                  "completion_tokens": u.completion_tokens,
                  "total_tokens": u.prompt_tokens + u.completion_tokens},
    }
```

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_codecs.py -q -k completions`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add wiwi/wire/openai_completions.py tests/test_codecs.py
git commit -m "Encode non-streaming completions responses"
```

---

### Task 3: Stream encoder

**Files:**
- Modify: `wiwi/wire/openai_completions.py`
- Test: `tests/test_codecs.py` (append)

**Interfaces:**
- Consumes: `wiwi.streaming.deltas` (`StreamStart`, `TextDelta`, `ThinkingDelta`,
  `ToolCallOpen`, `ToolCallArgsDelta`, `ToolCallClose`, `UsageFinal`, `Finish`,
  `StreamEnd`, `StreamError`), `wiwi.streaming.sse.sse_frame`.
- Produces: `CompletionStreamEncoder(model, req_id, include_usage=False)` with
  `feed(d) -> bytes | None` and `final_frame(usage=None, stop=None) -> bytes`.

- [ ] **Step 1: Write the failing tests**

```python
def _frames(enc, deltas):
    out = b""
    for d in deltas:
        chunk = enc.feed(d)
        if chunk:
            out += chunk
    return out


def test_completions_stream_emits_text_chunks_and_finish():
    from wiwi.streaming import deltas as dl
    enc = ocmpl.CompletionStreamEncoder("m", "r1")
    body = _frames(enc, [dl.StreamStart(model="m"), dl.TextDelta("he"),
                         dl.TextDelta("llo"), dl.UsageFinal(prompt=1, output=2),
                         dl.Finish(stop_reason="stop"), dl.StreamEnd()])
    text = body.decode()
    assert '"object": "text_completion"' in text
    assert '"text": "he"' in text and '"text": "llo"' in text
    finish = enc.final_frame().decode()
    assert '"finish_reason": "stop"' in finish
    # No ``[DONE]`` here: the shared stream wrapper appends it for style "chat".
    assert "[DONE]" not in finish
    # Usage rides only when the client asked for it.
    assert '"usage"' not in finish


def test_completions_stream_usage_frame_only_when_requested():
    from wiwi.streaming import deltas as dl
    enc = ocmpl.CompletionStreamEncoder("m", "r1", include_usage=True)
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.TextDelta("x"))
    enc.feed(dl.UsageFinal(prompt=7, output=3))
    enc.feed(dl.Finish(stop_reason="stop"))
    finish = enc.final_frame().decode()
    assert '"usage"' in finish and '"prompt_tokens": 7' in finish


def test_completions_stream_drops_thinking_and_tool_deltas():
    from wiwi.streaming import deltas as dl
    enc = ocmpl.CompletionStreamEncoder("m", "r1")
    enc.feed(dl.StreamStart(model="m"))
    assert enc.feed(dl.ThinkingDelta("hmm")) is None
    assert enc.feed(dl.ToolCallOpen(index=0, id="c1", name="f")) is None
    assert enc.feed(dl.ToolCallArgsDelta(index=0, args_fragment="{}")) is None
    assert enc.feed(dl.ToolCallClose(index=0)) is None
    enc.feed(dl.Finish(stop_reason="tool_calls"))
    assert '"finish_reason": "stop"' in enc.final_frame().decode()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_codecs.py -q -k "completions and stream"`
Expected: FAIL — `AttributeError: … 'CompletionStreamEncoder'`.

- [ ] **Step 3: Implement**

Append to `wiwi/wire/openai_completions.py`:

```python
class CompletionStreamEncoder:
    """IR deltas -> ``text_completion`` SSE frames.

    Mirrors ``openai_chat.ChatStreamEncoder``: the chunk skeleton is built once
    and only ``choices[0]`` is mutated per delta, so a token does not allocate a
    whole frame dict. There is no tool protocol and no reasoning field here, so
    ``ThinkingDelta`` and the ``ToolCall*`` family are dropped; a dropped call
    forces the finish reason to ``"stop"``.
    """

    def __init__(self, model: str, req_id: str, include_usage: bool = False):
        self.model = model
        self.req_id = req_id
        self._include_usage = include_usage
        self._usage: dl.UsageFinal | None = None
        self._stop = "stop"
        self._suppressed_tool = False
        self._chunk: dict[str, Any] = {
            "id": f"cmpl-{req_id}", "object": "text_completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "text": "", "finish_reason": None}],
        }
        self._choice = self._chunk["choices"][0]

    def _shell(self, text: str, finish: str | None = None) -> bytes:
        self._choice["text"] = text
        self._choice["finish_reason"] = finish
        self._chunk.pop("usage", None)
        return sse_frame("", orjson.dumps(self._chunk).decode())

    def feed(self, d: dl.IRStreamDelta) -> bytes | None:
        if isinstance(d, dl.StreamStart):
            return self._shell("")
        if isinstance(d, dl.TextDelta):
            return self._shell(d.text)
        if isinstance(d, (dl.ToolCallOpen, dl.ToolCallArgsDelta, dl.ToolCallClose)):
            if not isinstance(d, dl.ToolCallClose):
                self._suppressed_tool = True
            return None
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None
        if isinstance(d, dl.Finish):
            self._stop = d.stop_reason
            return None  # emitted by final_frame()
        if isinstance(d, dl.StreamError):
            err = {"error": {"message": d.message, "type": "api_error"}}
            return sse_frame("", orjson.dumps(err).decode())
        return None  # ThinkingDelta, StreamEnd: nothing to send

    def final_frame(self, usage: dl.UsageFinal | None = None,
                    stop: str | None = None) -> bytes:
        u = usage or self._usage or dl.UsageFinal()
        stop = stop or self._stop
        if self._suppressed_tool or tr.ir_to_openai_finish(stop) == "tool_calls":
            stop = "stop"
        out = self._shell("", finish=tr.ir_to_openai_finish(stop))
        if self._include_usage:
            self._choice["finish_reason"] = None
            self._chunk["choices"] = []
            out += sse_frame("", orjson.dumps({
                **self._chunk,
                "usage": {"prompt_tokens": u.prompt, "completion_tokens": u.output,
                          "total_tokens": u.prompt + u.output},
            }).decode())
        return out


def error_body(status: int, etype: str, message: str) -> dict[str, Any]:
    """OpenAI-shaped error envelope (identical to the chat dialect's)."""
    return {"error": {"message": message, "type": etype, "code": etype}}
```

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_codecs.py -q -k completions`
Expected: PASS (12 passed).

- [ ] **Step 5: Lint and commit**

```bash
ruff check wiwi/wire/openai_completions.py tests/test_codecs.py
git add wiwi/wire/openai_completions.py tests/test_codecs.py
git commit -m "Stream completions responses as text_completion frames"
```

---

### Task 4: Wire the surface into the server

**Files:**
- Modify: `wiwi/core/context.py` (the `Surface` literal, line 13)
- Modify: `wiwi/server/app.py` (imports, `_surface_for_path`, `_encoder_for`, route)

**Interfaces:**
- Consumes: `wiwi.wire.openai_completions` (Task 1–3).
- Produces: live `POST /v1/completions`.

- [ ] **Step 1: Write the failing end-to-end test**

Create `tests/test_fix_round113.py` — verify the number first with
`ls tests/test_fix_round*.py` and use the next unused one:

```python
"""Regression round 113 — the legacy /v1/completions surface."""

import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: openai
    provider: openai
    keys:
      - label: default
        key: sk-upstream
model_list:
  - model_name: gpt-3.5-turbo-instruct
    wiwi_params:
      provider: openai
      model: gpt-3.5-turbo-instruct
"""


async def _client():
    app = create_app(load_config_from_string(_CFG))
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app),
                         base_url="http://test",
                         headers={"Authorization": "Bearer sk-wiwi-master-test"})
    return mgr, client


@respx.mock
async def test_completions_non_streaming_end_to_end():
    respx.post("https://api.openai.com/v1/completions").mock(
        return_value=Response(200, json={
            "id": "cmpl-up", "object": "text_completion",
            "choices": [{"index": 0, "text": "hi there", "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3,
                      "total_tokens": 5}}))
    mgr, client = await _client()
    try:
        r = await client.post("/v1/completions",
                              json={"model": "gpt-3.5-turbo-instruct", "prompt": "hi"})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "text_completion"
    assert body["choices"][0]["text"] == "hi there"
    assert body["usage"]["total_tokens"] == 5


async def test_completions_rejects_multi_prompt_with_openai_error_envelope():
    mgr, client = await _client()
    try:
        r = await client.post("/v1/completions",
                              json={"model": "gpt-3.5-turbo-instruct",
                                    "prompt": ["a", "b"]})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert r.json()["error"]["code"] == "invalid_request_error"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_fix_round113.py -q`
Expected: FAIL — the first test 404s (no route), the second 404s instead of 400.

- [ ] **Step 3: Implement the wiring**

`wiwi/core/context.py` line 13:

```python
Surface = Literal["chat", "responses", "messages", "completions"]
```

`wiwi/server/app.py` — add the import beside the other wire imports (grep
`from wiwi.wire import` to place it identically):

```python
from wiwi.wire import openai_completions as ocmpl
```

`_surface_for_path` (app.py:1445) — add before the `return "chat"` default:

```python
        if path.startswith("/v1/completions"):
            return "completions"
```

`_encoder_for` (app.py:1888) — add after the `"chat"` branch:

```python
        if surface == "completions":
            return ocmpl.CompletionStreamEncoder(
                model, req_id, include_usage=include_usage), "chat"
```

Route — insert after the `messages_api` handler (app.py:2173-2178):

```python
    @app.post("/v1/completions")
    async def completions_api(request: Request):
        """Legacy OpenAI text completions: one prompt in, ``choices[].text`` out.

        Served through ``run_chat_like`` like every other surface, so auth,
        routing, retries, budgets, logging and billing are identical.
        """
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "completions", body,
                                   ocmpl.decode_request, ocmpl.encode_response)
```

`_error_body_for` needs no new branch: its default returns `oc.error_body`, which is the
correct envelope for this dialect.

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_fix_round113.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Deterministic tool-call suppression is observable in the log**

Append to `tests/test_fix_round113.py`:

```python
@respx.mock
async def test_completions_tool_stop_is_reported_as_stop():
    respx.post("https://api.openai.com/v1/completions").mock(
        return_value=Response(200, json={
            "id": "cmpl-up", "object": "text_completion",
            "choices": [{"index": 0, "text": "", "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}))
    mgr, client = await _client()
    try:
        r = await client.post("/v1/completions",
                              json={"model": "gpt-3.5-turbo-instruct", "prompt": "hi"})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.json()["choices"][0]["finish_reason"] == "stop"
```

Run: `python3 -m pytest tests/test_fix_round113.py -q` — PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add wiwi/core/context.py wiwi/server/app.py tests/test_fix_round113.py
git commit -m "Serve the legacy completions surface at /v1/completions"
```

---

### Task 5: Docs, then the gate

**Files:**
- Modify: `docs/API_REFERENCE.md`, `AGENTS.md`, `AUDIT.md`, `docs/MVP.md`, `docs/PLAN.md`

- [ ] **Step 1: Update the docs**

- `docs/API_REFERENCE.md`: add `POST /v1/completions` to the surface table with its
  request/response shape and the refused parameters.
- `AGENTS.md`: add `POST /v1/completions` to the inbound-surfaces list; change the wire
  directory row to name `openai_completions.py`.
- `AUDIT.md`: mark #213 (`/v1/completions` not implemented) `**Status: fixed**`; note that
  `/v1/embeddings` remains open.
- `docs/MVP.md` F1 and `docs/PLAN.md` Phase 2 item 3: both currently assert the route is
  **not** implemented — correct them to "shipped 2026-10-01".

- [ ] **Step 2: Run the full gate**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

Expected: both green. Record the pass count.

- [ ] **Step 3: Live smoke**

```bash
./start.sh          # or: python3 -m wiwi.main --config wiwi.yaml
curl -sS localhost:4000/v1/completions -H 'Authorization: Bearer <key>' \
  -H 'content-type: application/json' \
  -d '{"model":"<configured-group>","prompt":"Say hi in three words.","max_tokens":16}'
curl -sS -N localhost:4000/v1/completions -H 'Authorization: Bearer <key>' \
  -H 'content-type: application/json' \
  -d '{"model":"<configured-group>","prompt":"Count to five.","max_tokens":32,"stream":true,"stream_options":{"include_usage":true}}'
```

Expected: a `text_completion` body with `choices[0].text`; and for the streaming call,
`text_completion` chunks followed by the finish frame, a usage frame, then `data: [DONE]`.

- [ ] **Step 4: Commit**

```bash
git add docs/ API_REFERENCE.md AGENTS.md AUDIT.md && git commit -m "Document the completions surface"
```
