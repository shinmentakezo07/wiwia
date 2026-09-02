"""OpenAI Chat Completions wire codec: decode requests to IR, encode responses/streams."""

from __future__ import annotations

import json
import time
from typing import Any

import orjson

from wiwi.core.context import RequestContext
from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import sse_frame


class DialectError(ValueError):
    pass


def decode_request(body: dict[str, Any]) -> ir.Request:
    if not isinstance(body.get("model"), str) or not body["model"]:
        raise DialectError("'model' is required")
    if body.get("n") not in (None, 1):
        raise DialectError("'n' must be 1 (multiple choices unsupported)")
    messages = []
    for m in body.get("messages") or []:
        role = m.get("role", "user")
        if role == "developer":
            # OpenAI's newer name for system-level instructions; unify so
            # non-OpenAI upstreams fold it into the system prompt once.
            role = "system"
        content = m.get("content")
        parts: list[ir.Part] = []
        tool_calls = m.get("tool_calls") or []
        if isinstance(content, str):
            parts.append(ir.TextPart(content))
        elif isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue  # malformed item: skip rather than 500 on .get
                if c.get("type") == "text":
                    parts.append(ir.TextPart(c.get("text", "")))
                elif c.get("type") == "image_url":
                    url = (c.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        header, _, b64 = url.partition(",")
                        mime = header[5:].split(";")[0] or "image/png"
                        parts.append(ir.ImagePart(b64=b64, mime=mime))
                    else:
                        parts.append(ir.ImagePart(url=url))
                elif c.get("type") == "input_audio":
                    ia = c.get("input_audio") or {}
                    parts.append(ir.AudioPart(
                        b64=ia.get("data"),
                        mime=f"audio/{ia.get('format') or 'wav'}"))
        # Reasoning models (o1/o3, DeepSeek-R1, etc.) emit a separate
        # reasoning_content field on assistant messages. Lift it into a
        # ThinkingPart so the IR carries the thinking context forward —
        # without this, Anthropic extended-thinking breaks across turns when
        # a Claude-Code-via-OpenAI-shape client echoes prior assistant
        # messages with reasoning_content set.
        #
        # IMPORTANT: must come BEFORE the tool_calls loop below. The Anthropic
        # API requires the final assistant turn of a thinking-enabled request
        # to begin with a thinking block, and the IR's parts order is what
        # anthropic_adapter.encode_request iterates in. If ThinkingPart is
        # appended after ToolUsePart, Anthropic rejects the request.
        if role == "assistant":
            rc = m.get("reasoning_content")
            if isinstance(rc, str) and rc:
                # Must be the FIRST part: Anthropic extended-thinking requires
                # the final assistant turn to begin with a thinking block.
                parts.insert(0, ir.ThinkingPart(text=rc))
        for tc in tool_calls:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                from wiwi.streaming.partial_json import _repair_truncated_json
                try:
                    args = json.loads(_repair_truncated_json(raw_args))
                except json.JSONDecodeError:
                    args = {}
            parts.append(ir.ToolUsePart(id=tc.get("id", ""), name=fn.get("name", ""),
                                        args=args, raw_args=raw_args))
        if role == "tool":
            # OpenAI spec: tool message content is a string. Defensively coerce
            # non-string payloads (None, lists, dicts) so downstream adapters
            # — which all type ToolResultPart.content as str — never receive a
            # list/dict that would be JSON-serialized into the wire body in a
            # way the upstream provider rejects.
            tool_images: list[ir.ImagePart] = []
            if content is None:
                tool_content: str = ""
            elif isinstance(content, str):
                tool_content = content
            elif isinstance(content, list):
                # Concatenate text blocks if present; fall back to str() for
                # any other shape. Common Anthropic-style list payloads are
                # not valid here, so this is a best-effort recovery.
                pieces = [b.get("text", "") for b in content
                          if isinstance(b, dict) and b.get("type") == "text"]
                tool_content = "\n".join(pieces) if pieces else orjson.dumps(content).decode()
                # Multimodal tool results: collect image blocks (base64 data
                # URLs or remote URLs) so providers with native image support
                # can re-emit them.
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "image_url":
                        continue
                    url = (b.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        header, _, b64 = url.partition(",")
                        mime = header[5:].split(";")[0] or "image/png"
                        tool_images.append(ir.ImagePart(b64=b64, mime=mime))
                    elif url:
                        tool_images.append(ir.ImagePart(url=url))
            else:
                tool_content = str(content)
            parts = [ir.ToolResultPart(tool_use_id=m.get("tool_call_id", ""),
                                       content=tool_content,
                                       images=tool_images)]
        if not parts and role != "assistant":
            parts = [ir.TextPart("")]
        messages.append(ir.Message(role=role, parts=parts))  # type: ignore[arg-type]

    tools = [
        ir.Tool(name=(fn := t.get("function") or {}).get("name", ""),
                description=fn.get("description", ""),
                parameters_json_schema=fn.get("parameters") or {"type": "object"},
                strict=fn.get("strict"))
        for t in body.get("tools") or [] if t.get("type") == "function"
    ]
    tc_raw = body.get("tool_choice")
    tool_choice: ir.ToolChoice | None = None
    if tc_raw == "auto":
        tool_choice = ir.ToolChoiceAuto()
    elif tc_raw == "none":
        tool_choice = ir.ToolChoiceNone()
    elif tc_raw == "required":
        tool_choice = ir.ToolChoiceRequired()
    elif isinstance(tc_raw, dict) and tc_raw.get("type") == "function":
        tool_choice = ir.ToolChoiceNamed((tc_raw.get("function") or {}).get("name", ""))

    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=(body.get("max_tokens") if body.get("max_tokens") is not None
                    else body.get("max_completion_tokens")),
        stop=[body["stop"]] if isinstance(body.get("stop"), str) else (body.get("stop") or []),
        seed=body.get("seed"),
        n=body.get("n") or 1,
        parallel_tool_calls=body.get("parallel_tool_calls"),
        # OpenAI has no disable_parallel_tool_use; derive from parallel_tool_calls=False.
        # parallel_tool_calls=false means parallel is disabled → disable_parallel_tool_use=true.
        disable_parallel_tool_use=(True if body.get("parallel_tool_calls") is False else None),
        reasoning_effort=body.get("reasoning_effort"),
    )
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema"):
        js = rf.get("json_schema") or {}
        g.response_format = ir.ResponseFormat(type=rf["type"],
                                              json_schema=js.get("schema"),
                                              name=js.get("name"),
                                              strict=js.get("strict"))
    stream_opts = body.get("stream_options") or {}
    return ir.Request(
        model=body["model"], messages=messages, tools=tools, tool_choice=tool_choice,
        gen_params=g, stream=bool(body.get("stream")),
        stream_options_include_usage=bool(stream_opts.get("include_usage", False)),
        extras={k: v for k, v in body.items() if k not in _KNOWN_KEYS},
    )


_KNOWN_KEYS = {"model", "messages", "tools", "tool_choice", "temperature", "top_p",
               "max_tokens", "max_completion_tokens", "stop", "seed", "n", "stream",
               "stream_options", "response_format", "parallel_tool_calls",
               "reasoning_effort"}


def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant",
                               "content": turn.text if turn.text else None}
    # A1: provider-hosted builtin calls (web_search) are suppressed — the
    # client would otherwise try to execute a phantom function.
    tool_calls = [t for t in turn.tool_calls if not bt.is_builtin_name(t.name)]
    if tool_calls:
        message["tool_calls"] = [
            {"id": t.id, "type": "function",
             "function": {"name": t.name,
                          "arguments": t.raw_args or json.dumps(t.args)}}
            for t in tool_calls
        ]
    # Reasoning models emit a separate reasoning_content field; include it
    # when the provider returned thinking blocks so downstream clients that
    # look for reasoning_content (e.g. OpenAI-shaped clients) receive it.
    if turn.thinking:
        message["reasoning_content"] = "".join(t.text for t in turn.thinking)
    u = turn.usage
    usage = {
        "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
        "total_tokens": u.prompt_tokens + u.completion_tokens,
        "prompt_tokens_details": {"cached_tokens": u.cached_tokens},
        "completion_tokens_details": {"reasoning_tokens": u.reasoning_tokens},
    }
    fr = {"stop": "stop", "length": "length", "tool_call": "tool_calls",
          "content_filter": "content_filter"}.get(turn.stop_reason, "stop")
    # A1 downgrade guard: tool_calls finish with every call suppressed is wrong.
    if fr == "tool_calls" and not tool_calls:
        fr = "stop"
    return {
        "id": f"chatcmpl-{req_id}", "object": "chat.completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": fr}],
        "usage": usage,
    }


class ChatStreamEncoder:
    """IR deltas -> chat.completion.chunk frames (docs/CORE.md §7.2 fsm_chat)."""

    def __init__(self, model: str, req_id: str, include_usage: bool = False):
        self.model = model
        self.req_id = req_id
        # OpenAI semantics: usage rides only in a final chunk with an EMPTY
        # choices array, and only when the client asked for it via
        # stream_options.include_usage (G4).
        self._include_usage = include_usage
        self._started = False
        self._finished = False
        self._usage: dl.UsageFinal | None = None
        self._stop: str = "stop"
        # A1 stop_reason guard: a builtin tool call was suppressed (so the
        # provider's tool_call finish may no longer have a call behind it).
        self._suppressed_builtin = False
        # Any real (non-builtin) tool_calls frame emitted?
        self._saw_tool_calls = False
        # Tool indices that emitted an Open (legal or suppressed): ArgsDelta
        # for a suppressed builtin must not emit a phantom args-only frame.
        self._tool_indices: set[int] = set()
        # Chunk skeleton built once: id/object/created/model never change for
        # the life of the stream, so only `choices[0]` is mutated per delta
        # instead of re-allocating the whole chunk dict on every token.
        self._chunk: dict[str, Any] = {
            "id": f"chatcmpl-{req_id}", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": None, "finish_reason": None}],
        }
        self._choice = self._chunk["choices"][0]

    def _shell(self, delta: dict[str, Any], finish: str | None = None) -> bytes:
        self._choice["delta"] = delta
        self._choice["finish_reason"] = finish
        self._chunk.pop("usage", None)
        return sse_frame("", orjson.dumps(self._chunk).decode())

    def feed(self, d: dl.IRStreamDelta) -> bytes | None:
        if isinstance(d, dl.StreamStart):
            self._started = True
            return self._shell({"role": "assistant", "content": ""})
        if isinstance(d, dl.TextDelta):
            return self._shell({"content": d.text})
        if isinstance(d, dl.ThinkingDelta):
            if not d.text:
                return None  # signature-only delta: no content to emit
            return self._shell({"reasoning_content": d.text})
        if isinstance(d, dl.ToolCallOpen):
            # A1: provider-hosted builtin calls (web_search) are suppressed —
            # a function tool_calls frame would invite the client to execute
            # a phantom function. The index stays unregistered so the paired
            # ArgsDelta drops too (no args-only phantom frame).
            if d.builtin is not None:
                self._suppressed_builtin = True
                return None
            self._tool_indices.add(d.index)
            # `id` must be a string; some providers return integer ids.
            tool_id = d.id if isinstance(d.id, str) else str(d.id)
            self._saw_tool_calls = True
            return self._shell({"tool_calls": [{
                "index": d.index, "id": tool_id, "type": "function",
                "function": {"name": d.name, "arguments": ""}}]})
        if isinstance(d, dl.ToolCallArgsDelta):
            if d.index not in self._tool_indices:
                # ArgsDelta with no preceding Open: contract-illegal; drop
                # rather than emit a phantom args-only frame.
                return None
            return self._shell({"tool_calls": [{
                "index": d.index, "function": {"arguments": d.args_fragment}}]})
        if isinstance(d, dl.ToolCallClose):
            return None
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None  # emitted with the Finish frame
        if isinstance(d, dl.Finish):
            self._stop = d.stop_reason
            # A1 downgrade guard: only when suppression actually removed the
            # only tool call. A plain tool_call finish with no calls seen is
            # upstream behavior — pass it through untouched.
            if (self._stop == "tool_call" and self._suppressed_builtin
                    and not self._saw_tool_calls):
                self._stop = "stop"
            return None  # final_frame() emits finish_reason + usage together
        if isinstance(d, dl.StreamEnd):
            return None  # [DONE] is emitted by the caller after final_frame()
        if isinstance(d, dl.StreamError):
            # error frame only: connection close terminates the stream, same
            # as the anthropic/responses encoders — [DONE] would imply success
            err = {"error": {"message": d.message, "type": "api_error"}}
            return sse_frame("", orjson.dumps(err).decode())
        return None

    def final_frame(self, usage: dl.UsageFinal | None = None,
                    stop: str | None = None) -> bytes:
        u = usage or getattr(self, "_usage", None) or dl.UsageFinal()
        stop = stop or getattr(self, "_stop", "stop")
        fr = {"stop": "stop", "length": "length", "tool_call": "tool_calls",
              "content_filter": "content_filter"}.get(stop, "stop")
        out = self._shell({}, finish=fr)
        if self._include_usage:
            # OpenAI semantics: usage arrives in a final chunk carrying an
            # EMPTY choices array (after the finish_reason chunk), not in the
            # finish_reason chunk itself.
            self._choice["delta"] = None
            self._choice["finish_reason"] = None
            self._chunk["choices"] = []
            out += sse_frame("", orjson.dumps({
                **self._chunk,
                "usage": {
                    "prompt_tokens": u.prompt, "completion_tokens": u.output,
                    "total_tokens": u.prompt + u.output,
                    "prompt_tokens_details": {"cached_tokens": u.cached},
                    "completion_tokens_details": {"reasoning_tokens": u.reasoning},
                }}).decode())
        return out


def error_body(status: int, etype: str, message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": etype, "code": etype}}
