"""OpenAI legacy Completions wire codec: ``POST /v1/completions``.

The oldest OpenAI wire shape: a bare ``prompt`` string and ``choices[].text``
instead of a chat transcript. No roles and no tool protocol, so the prompt maps
onto a single user message and the shared pipeline does everything else (auth,
routing, retries, budget, logs, billing).

Refusals here are deliberate and loud. ``logprobs``, ``best_of > 1``, ``echo`` and
a token-id ``prompt`` have no IR representation, and silently dropping a parameter
the caller sent is the failure class ``UPDATE.md`` exists to record.
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
from wiwi.wire.openai_chat import DialectError, _stop_list

_KNOWN_KEYS = {"model", "prompt", "suffix", "max_tokens", "max_completion_tokens",
               "temperature", "top_p", "stop", "seed", "n", "stream", "stream_options"}


def _max_token_cap(body: dict[str, Any]) -> int | None:
    """Resolve the output-token cap, preferring a usable ``max_tokens``.

    ``max_completion_tokens`` is the newer alias. Presence and usability differ:
    selecting on ``is not None`` would pick ``max_tokens`` first and then let a
    non-numeric value coerce to ``None``, discarding a good alias beside it.
    Resolve to an int first, and only consult the alias when that is genuinely
    ``None`` (so ``max_tokens: 0`` remains a real cap).
    """
    primary = ir.coerce_int(body.get("max_tokens"))
    if primary is not None:
        return primary
    return ir.coerce_int(body.get("max_completion_tokens"))


def _prompt_text(body: dict[str, Any]) -> str:
    """Resolve the wire ``prompt`` to the text of one user turn.

    The wire allows a string, a list of strings (batch), or a list of token ids.
    Only a single string maps onto the IR: a batch of more than one and a token-id
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
        # ``suffix`` is code infill: the model continues the prompt *before* this
        # text. The IR has one text channel, so concatenate — the upstream sees
        # the same bytes either way.
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


def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]:
    """Non-streaming body: ``choices[].text`` plus usage.

    Tool calls are dropped and the finish reason forced to ``"stop"``: this
    dialect has no tool protocol, so a ``tool_calls`` finish with no call body
    would be a turn the client can neither run nor complete (the analogue of
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


class CompletionStreamEncoder:
    """IR deltas -> ``text_completion`` SSE frames.

    Mirrors ``openai_chat.ChatStreamEncoder``: the chunk skeleton is built once and
    only ``choices[0]`` is mutated per delta, so a token does not allocate a whole
    frame dict. There is no tool protocol and no reasoning field on this surface,
    so ``ThinkingDelta`` and the ``ToolCall*`` family are dropped; a dropped call
    forces the finish reason to ``"stop"``. ``[DONE]`` is appended by the shared
    stream wrapper for style ``"chat"``, not here.
    """

    def __init__(self, model: str, req_id: str, include_usage: bool = False):
        self.model = model
        self.req_id = req_id
        # OpenAI semantics: usage rides only in a final frame, and only when the
        # client asked for it via ``stream_options.include_usage`` (G4).
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
            # Error frame only: connection close terminates the stream, same as
            # the chat/anthropic/responses encoders.
            err = {"error": {"message": d.message, "type": "api_error"}}
            return sse_frame("", orjson.dumps(err).decode())
        return None  # ThinkingDelta, ServerToolResultDelta, StreamEnd: nothing to send

    def final_frame(self, usage: dl.UsageFinal | None = None,
                    stop: str | None = None) -> bytes:
        u = usage or self._usage or dl.UsageFinal()
        stop = stop or self._stop
        # Consult the finish on this path too: a failover/resume rebuilds the
        # frame from state that never went through feed(Finish).
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
