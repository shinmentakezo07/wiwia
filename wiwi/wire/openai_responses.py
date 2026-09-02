"""OpenAI Responses wire codec: /v1/responses decode + stream events (fsm_responses).

Stateless mode: every request is self-contained (Codex sends full history with
store:false). previous_response_id is rejected with a clear error (post-MVP).
"""

from __future__ import annotations

import json
import time
from typing import Any

import orjson

from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import sse_frame
from wiwi.wire.openai_chat import DialectError


def decode_request(body: dict[str, Any]) -> ir.Request:
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise DialectError("'model' is required")
    if body.get("previous_response_id"):
        raise DialectError("previous_response_id is not supported yet; send full input")

    messages: list[ir.Message] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append(ir.Message(role="system", parts=[ir.TextPart(instructions)]))

    raw_input = body.get("input")
    items: list[dict[str, Any]]
    if isinstance(raw_input, str):
        items = [{"type": "message", "role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        items = raw_input
    else:
        items = []

    for item in items:
        itype = item.get("type", "message")
        if itype == "message":
            role = item.get("role", "user")
            content = item.get("content")
            parts: list[ir.Part] = []
            if isinstance(content, str):
                parts.append(ir.TextPart(content))
            elif isinstance(content, list):
                for c in content:
                    ctype = c.get("type", "output_text" if role == "assistant" else "input_text")
                    if ctype in ("input_text", "output_text", "text"):
                        parts.append(ir.TextPart(c.get("text", "")))
                    elif ctype == "input_image":
                        url = c.get("image_url") or ""
                        if url.startswith("data:"):
                            header, _, b64 = url.partition(",")
                            mime = header[5:].split(";")[0] or "image/png"
                            parts.append(ir.ImagePart(b64=b64, mime=mime))
                        else:
                            parts.append(ir.ImagePart(url=url))
            ir_role = ("system" if role in ("system", "developer")
                       else "assistant" if role == "assistant" else "user")
            messages.append(ir.Message(role=ir_role, parts=parts))
        elif itype == "function_call":
            raw_args = item.get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                from wiwi.streaming.partial_json import _repair_truncated_json
                try:
                    args = json.loads(_repair_truncated_json(raw_args))
                except json.JSONDecodeError:
                    args = {}
            messages.append(ir.Message(role="assistant", parts=[
                ir.ToolUsePart(id=item.get("call_id", ""), name=item.get("name", ""),
                               args=args, raw_args=raw_args)]))
        elif itype == "function_call_output":
            output = item.get("output")
            if isinstance(output, str):
                text = output
            elif isinstance(output, list):
                text = " ".join(c.get("text", "") for c in output
                                if isinstance(c, dict)
                                and c.get("type") in ("output_text", "text")) \
                    or json.dumps(output)
            elif output is None:
                text = ""
            else:
                text = json.dumps(output)
            messages.append(ir.Message(role="tool", parts=[
                ir.ToolResultPart(tool_use_id=item.get("call_id", ""), content=text)]))
        elif itype == "reasoning":
            summary = item.get("summary") or []
            text = " ".join(s.get("text", "") for s in summary if isinstance(s, dict))
            if text:
                messages.append(ir.Message(role="assistant",
                                           parts=[ir.ThinkingPart(text)]))

    tools = []
    for t in body.get("tools") or []:
        if t.get("type") == "function":
            tools.append(ir.Tool(name=t.get("name", ""),
                                 description=t.get("description", ""),
                                 parameters_json_schema=t.get("parameters")
                                 or {"type": "object"},
                                 strict=t.get("strict")))
    tc_raw = body.get("tool_choice")
    tool_choice: ir.ToolChoice | None = None
    if tc_raw == "auto":
        tool_choice = ir.ToolChoiceAuto()
    elif tc_raw == "none":
        tool_choice = ir.ToolChoiceNone()
    elif tc_raw == "required":
        tool_choice = ir.ToolChoiceRequired()
    elif isinstance(tc_raw, dict) and tc_raw.get("type") == "function":
        tool_choice = ir.ToolChoiceNamed(tc_raw.get("name", ""))

    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=(body.get("max_output_tokens")),
        parallel_tool_calls=body.get("parallel_tool_calls"),
        disable_parallel_tool_use=(True if body.get("parallel_tool_calls") is False else None),
        reasoning_effort=((body.get("reasoning") or {}).get("effort")
                          if isinstance(body.get("reasoning"), dict) else None),
    )
    rf = body.get("text") or {}
    if isinstance(rf, dict):
        fmt = rf.get("format") or {}
        if fmt.get("type") == "json_schema":
            g.response_format = ir.ResponseFormat(type="json_schema",
                                                  json_schema=fmt.get("schema"),
                                                  name=fmt.get("name"),
                                                  strict=fmt.get("strict"))
        elif fmt.get("type") == "json_object":
            g.response_format = ir.ResponseFormat(type="json_object")
    return ir.Request(model=model, messages=messages, tools=tools,
                      tool_choice=tool_choice, gen_params=g,
                      stream=bool(body.get("stream")))


def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    out_id = 0
    for t in turn.thinking:
        output.append({"type": "reasoning", "id": f"rs_{req_id}_{out_id}",
                       "summary": [{"type": "summary_text", "text": t.text}]})
        out_id += 1
    if turn.text or not turn.tool_calls:
        output.append({"type": "message", "id": f"msg_{req_id}", "status": "completed",
                       "role": "assistant",
                       "content": [{"type": "output_text",
                                    "text": turn.text,
                                    "annotations": []}]})
        out_id += 1
    for t in turn.tool_calls:
        output.append({"type": "function_call", "id": f"fc_{req_id}_{out_id}",
                       "call_id": t.id, "name": t.name,
                       "arguments": t.raw_args or json.dumps(t.args)})
        out_id += 1
    u = turn.usage
    # Truncated output surfaces as status "incomplete" + incomplete_details,
    # mirroring the streaming path's response.incomplete terminal event.
    status = "incomplete" if turn.stop_reason == "length" else "completed"
    resp: dict[str, Any] = {
        "id": f"resp_{req_id}", "object": "response", "created_at": int(time.time()),
        "status": status, "model": model, "output": output,
        "usage": {
            "input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
            "total_tokens": u.prompt_tokens + u.completion_tokens,
            "input_tokens_details": {"cached_tokens": u.cached_tokens},
            "output_tokens_details": {"reasoning_tokens": u.reasoning_tokens},
        },
    }
    if status == "incomplete":
        resp["incomplete_details"] = {"reason": "max_output_tokens"}
    return resp


class ResponsesStreamEncoder:
    """IR deltas -> Responses SSE events (docs/CORE.md §7.2 fsm_responses)."""

    def __init__(self, model: str, req_id: str):
        self.model = model
        self.req_id = req_id
        self._seq = 0
        self._item_open: str | None = None   # "message" | "thinking" | "tool"
        self._open_out = -1                  # output_index of the currently open item
        self._usage: dl.UsageFinal | None = None
        self._stop = "stop"
        # Piece lists rather than repeated `+=`: joining once at close is
        # O(n) total instead of O(n^2) copying for a long stream.
        self._text_buf: list[str] = []
        self._think_buf: list[str] = []
        # Per-tool-call state keyed by the IR stream's tool index, so
        # interleaved parallel tool calls (Open(0) Open(1) Args(0) ...) don't
        # corrupt each other's item ids / argument buffers.
        self._tools: dict[int, dict[str, Any]] = {}
        self._open_tool: int | None = None
        # Closed item payloads (the dicts emitted by output_item.done), so the
        # terminal response.completed/response.incomplete event can carry the
        # full output array as the spec (and Codex CLI) require.
        self._output: list[dict[str, Any]] = []

    def _next_output_index(self) -> int:
        self._open_out += 1
        return self._open_out

    def _evt(self, etype: str, payload: dict[str, Any]) -> bytes:
        payload = {"type": etype, "sequence_number": self._seq, **payload}
        self._seq += 1
        return sse_frame("", orjson.dumps(payload).decode())

    def _close_tool(self, index: int) -> list[bytes]:
        """Close a specific tool item by IR index (parallel-safe)."""
        if self._item_open == "tool" and self._open_tool == index:
            self._item_open = None
            self._open_tool = None
        t = self._tools.pop(index, None)
        if t is None:
            return []
        idx = t["output_index"]
        n = t["index"]
        item_id = f"fc_{self.req_id}_{n}"
        item = {"type": "function_call", "id": item_id,
                "call_id": t["call_id"], "name": t["name"],
                "arguments": t["args"]}
        self._output.append(item)
        return [self._evt("response.function_call_arguments.done", {
            "item_id": item_id, "output_index": idx,
            "arguments": t["args"]}),
            self._evt("response.output_item.done", {
                "output_index": idx, "item": item})]

    def _close_item(self) -> list[bytes]:
        if self._item_open is None:
            return []
        kind = self._item_open
        idx = self._open_out
        self._item_open = None
        if kind == "message":
            text = "".join(self._text_buf)
            item = {"type": "message", "id": f"msg_{self.req_id}",
                    "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": text,
                                 "annotations": []}]}
            self._output.append(item)
            return [self._evt("response.output_text.done", {
                        "item_id": f"msg_{self.req_id}", "output_index": idx,
                        "content_index": 0, "text": text}),
                    self._evt("response.content_part.done", {
                        "item_id": f"msg_{self.req_id}", "output_index": idx,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": text,
                                 "annotations": []}}),
                    self._evt("response.output_item.done", {
                        "output_index": idx, "item": item})]
        if kind == "thinking":
            think = "".join(self._think_buf)
            item = {"type": "reasoning", "id": f"rs_{self.req_id}_{idx}",
                    "summary": [{"type": "summary_text", "text": think}]}
            self._output.append(item)
            return [self._evt("response.reasoning_summary_text.done", {
                        "item_id": f"rs_{self.req_id}_{idx}", "output_index": idx,
                        "text": think}),
                    self._evt("response.output_item.done", {
                        "output_index": idx, "item": item})]
        # kind == "tool"
        t = self._tools.get(self._open_tool) if self._open_tool is not None else None
        if t is None:
            self._item_open = None
            return []
        idx = t["output_index"]
        n = t["index"]
        item_id = f"fc_{self.req_id}_{n}"
        item = {"type": "function_call", "id": item_id,
                "call_id": t["call_id"], "name": t["name"],
                "arguments": t["args"]}
        self._output.append(item)
        return [self._evt("response.function_call_arguments.done", {
            "item_id": item_id, "output_index": idx,
            "arguments": t["args"]}),
            self._evt("response.output_item.done", {
                "output_index": idx, "item": item})]

    def feed(self, d: dl.IRStreamDelta) -> bytes | None:
        if isinstance(d, dl.StreamStart):
            return self._evt("response.created", {
                "response": {"id": f"resp_{self.req_id}", "object": "response",
                             "status": "in_progress", "model": self.model,
                             "output": []}})
        if isinstance(d, dl.TextDelta):
            out = []
            if self._item_open != "message":
                out.extend(self._close_item())
                oi = self._next_output_index()
                out.append(self._evt("response.output_item.added", {
                    "output_index": oi,
                    "item": {"type": "message", "id": f"msg_{self.req_id}",
                             "status": "in_progress", "role": "assistant",
                             "content": []}}))
                out.append(self._evt("response.content_part.added", {
                    "item_id": f"msg_{self.req_id}", "output_index": oi,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []}}))
                self._item_open = "message"
                self._text_buf = []
            self._text_buf.append(d.text)
            out.append(self._evt("response.output_text.delta", {
                "item_id": f"msg_{self.req_id}", "output_index": self._open_out,
                "content_index": 0, "delta": d.text}))
            return b"".join(out)
        if isinstance(d, dl.ThinkingDelta):
            if not d.text:
                return None  # signature-only delta: no Responses representation
            out = []
            if self._item_open != "thinking":
                out.extend(self._close_item())
                oi = self._next_output_index()
                out.append(self._evt("response.output_item.added", {
                    "output_index": oi,
                    "item": {"type": "reasoning", "id": f"rs_{self.req_id}_{oi}",
                             "summary": []}}))
                self._item_open = "thinking"
                self._think_buf = []
            self._think_buf.append(d.text)
            out.append(self._evt("response.reasoning_summary_text.delta", {
                "item_id": f"rs_{self.req_id}_{self._open_out}",
                "output_index": self._open_out, "delta": d.text}))
            return b"".join(out)
        if isinstance(d, dl.ToolCallOpen):
            out: list[bytes] = []
            # Only close the open item if it's a message/thinking — parallel
            # tool calls are siblings, not sequential; don't prematurely close
            # an already-open tool.
            if self._item_open is not None and self._item_open != "tool":
                out.extend(self._close_item())
            n = d.index
            oi = self._next_output_index()
            self._tools[n] = {"index": n, "name": d.name,
                              "call_id": d.id, "args": "", "output_index": oi}
            self._open_tool = n
            out.append(self._evt("response.output_item.added", {
                "output_index": oi,
                "item": {"type": "function_call", "id": f"fc_{self.req_id}_{n}",
                         "call_id": d.id, "name": d.name, "arguments": ""}}))
            self._item_open = "tool"
            return b"".join(out)
        if isinstance(d, dl.ToolCallArgsDelta):
            t = self._tools.get(d.index)
            if t is None:
                # No Open preceded this ArgsDelta: the IR contract forbids it,
                # and synthesizing an entry would collide with the currently
                # open item's output_index. Drop the fragment (mirrors the
                # Anthropic encoder's defensive drop).
                return None
            t["args"] += d.args_fragment
            self._open_tool = d.index
            return self._evt("response.function_call_arguments.delta", {
                "item_id": f"fc_{self.req_id}_{d.index}",
                "output_index": t["output_index"],
                "delta": d.args_fragment})
        if isinstance(d, dl.ToolCallClose):
            return b"".join(self._close_tool(d.index))
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None
        if isinstance(d, dl.Finish):
            self._stop = d.stop_reason
            return None
        if isinstance(d, dl.StreamEnd):
            # caller emits response.completed via _completed() after the loop
            return b"".join(self._close_item())
        if isinstance(d, dl.StreamError):
            return self._evt("response.failed", {
                "response": {"id": f"resp_{self.req_id}", "status": "failed",
                             "error": {"code": "api_error", "message": d.message}}})
        return None

    def _completed(self) -> bytes:
        # A legal stream always closes the currently open output item before
        # the terminal event, even when the last delta left one open.
        closing = b"".join(self._close_item())
        u = self._usage or dl.UsageFinal()
        # Truncation is a DISTINCT terminal event (response.incomplete) with
        # status "incomplete" + incomplete_details — not a completed response.
        incomplete = self._stop == "length"
        etype = "response.incomplete" if incomplete else "response.completed"
        status = "incomplete" if incomplete else "completed"
        resp: dict[str, Any] = {"id": f"resp_{self.req_id}", "object": "response",
                                "status": status, "model": self.model,
                                "output": self._output,
                                "usage": {
                                    "input_tokens": u.prompt, "output_tokens": u.output,
                                    "total_tokens": u.prompt + u.output,
                                    "input_tokens_details": {"cached_tokens": u.cached},
                                    "output_tokens_details": {"reasoning_tokens": u.reasoning}}}
        if incomplete:
            resp["incomplete_details"] = {"reason": "max_output_tokens"}
        return closing + self._evt(etype, {"response": resp})


def error_body(status: int, etype: str, message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": etype, "code": etype,
                      "param": None}}
