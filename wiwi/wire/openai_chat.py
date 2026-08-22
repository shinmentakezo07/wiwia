"""OpenAI Chat Completions wire codec: decode requests to IR, encode responses/streams."""

from __future__ import annotations

import json
import time
from typing import Any

import orjson

from wiwi.core.context import RequestContext
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
        content = m.get("content")
        parts: list[ir.Part] = []
        tool_calls = m.get("tool_calls") or []
        if isinstance(content, str):
            parts.append(ir.TextPart(content))
        elif isinstance(content, list):
            for c in content:
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
        for tc in tool_calls:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
            parts.append(ir.ToolUsePart(id=tc.get("id", ""), name=fn.get("name", ""),
                                        args=args, raw_args=raw_args))
        if role == "tool":
            parts = [ir.ToolResultPart(tool_use_id=m.get("tool_call_id", ""),
                                       content=str(content))]
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
        max_tokens=body.get("max_tokens") or body.get("max_completion_tokens"),
        stop=[body["stop"]] if isinstance(body.get("stop"), str) else (body.get("stop") or []),
        seed=body.get("seed"),
        n=body.get("n") or 1,
        parallel_tool_calls=body.get("parallel_tool_calls"),
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
        stream_options_include_usage=bool(stream_opts.get("include_usage",
                                                          bool(body.get("stream")))),
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
    if turn.tool_calls:
        message["tool_calls"] = [
            {"id": t.id, "type": "function",
             "function": {"name": t.name,
                          "arguments": t.raw_args or json.dumps(t.args)}}
            for t in turn.tool_calls
        ]
    u = turn.usage
    usage = {
        "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
        "total_tokens": u.prompt_tokens + u.completion_tokens,
        "prompt_tokens_details": {"cached_tokens": u.cached_tokens},
        "completion_tokens_details": {"reasoning_tokens": u.reasoning_tokens},
    }
    fr = {"stop": "stop", "length": "length", "tool_call": "tool_calls",
          "content_filter": "content_filter"}.get(turn.stop_reason, "stop")
    return {
        "id": f"chatcmpl-{req_id}", "object": "chat.completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": fr}],
        "usage": usage,
    }


class ChatStreamEncoder:
    """IR deltas -> chat.completion.chunk frames (docs/CORE.md §7.2 fsm_chat)."""

    def __init__(self, model: str, req_id: str):
        self.model = model
        self.req_id = req_id
        self._started = False
        self._finished = False

    def _shell(self, delta: dict[str, Any], finish: str | None = None,
               usage: dict[str, Any] | None = None) -> bytes:
        obj: dict[str, Any] = {
            "id": f"chatcmpl-{self.req_id}", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            obj["usage"] = usage
        return sse_frame("", orjson.dumps(obj).decode())

    def feed(self, d: dl.IRStreamDelta) -> bytes | None:
        if isinstance(d, dl.StreamStart):
            self._started = True
            return self._shell({"role": "assistant", "content": ""})
        if isinstance(d, dl.TextDelta):
            return self._shell({"content": d.text})
        if isinstance(d, dl.ThinkingDelta):
            return self._shell({"reasoning_content": d.text})
        if isinstance(d, dl.ToolCallOpen):
            return self._shell({"tool_calls": [{
                "index": d.index, "id": d.id, "type": "function",
                "function": {"name": d.name, "arguments": ""}}]})
        if isinstance(d, dl.ToolCallArgsDelta):
            return self._shell({"tool_calls": [{
                "index": d.index, "function": {"arguments": d.args_fragment}}]})
        if isinstance(d, dl.ToolCallClose):
            return None
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None  # emitted with the Finish frame
        if isinstance(d, dl.Finish):
            self._stop = d.stop_reason
            return None  # final_frame() emits finish_reason + usage together
        if isinstance(d, dl.StreamEnd):
            return None  # [DONE] is emitted by the caller after final_frame()
        if isinstance(d, dl.StreamError):
            err = {"error": {"message": d.message, "type": "api_error"}}
            return sse_frame("", orjson.dumps(err).decode()) + b"data: [DONE]\n\n"
        return None

    def final_frame(self, usage: dl.UsageFinal | None = None,
                    stop: str | None = None) -> bytes:
        u = usage or getattr(self, "_usage", None) or dl.UsageFinal()
        stop = stop or getattr(self, "_stop", "stop")
        usage_obj = {
            "prompt_tokens": u.prompt, "completion_tokens": u.output,
            "total_tokens": u.prompt + u.output,
            "prompt_tokens_details": {"cached_tokens": u.cached},
            "completion_tokens_details": {"reasoning_tokens": u.reasoning},
        }
        fr = {"stop": "stop", "length": "length", "tool_call": "tool_calls",
              "content_filter": "content_filter"}.get(stop, "stop")
        return self._shell({}, finish=fr, usage=usage_obj)


def error_body(status: int, etype: str, message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": etype, "code": etype}}
