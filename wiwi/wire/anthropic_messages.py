"""Anthropic Messages wire codec: /v1/messages decode + SSE encode (fsm_anthropic)."""

from __future__ import annotations

import json
from typing import Any

import orjson

from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import sse_frame
from wiwi.wire.openai_chat import DialectError


def decode_request(body: dict[str, Any]) -> ir.Request:
    if not isinstance(body.get("model"), str) or not body["model"]:
        raise DialectError("'model' is required")
    messages: list[ir.Message] = []
    system_text = body.get("system")
    if isinstance(system_text, str) and system_text:
        messages.append(ir.Message(role="system", parts=[ir.TextPart(system_text)]))
    elif isinstance(system_text, list):
        parts = [ir.TextPart(b.get("text", ""), cache_control=b.get("cache_control"))
                 for b in system_text if b.get("type") == "text"]
        if parts:
            messages.append(ir.Message(role="system", parts=parts))
    for m in body.get("messages") or []:
        role = m.get("role", "user")
        content = m.get("content")
        parts: list[ir.Part] = []
        if isinstance(content, str):
            parts.append(ir.TextPart(content))
        elif isinstance(content, list):
            for b in content:
                btype = b.get("type")
                if btype == "text":
                    parts.append(ir.TextPart(b.get("text", ""),
                                             cache_control=b.get("cache_control")))
                elif btype == "image":
                    src = b.get("source") or {}
                    if src.get("type") == "base64":
                        parts.append(ir.ImagePart(b64=src.get("data"),
                                                  mime=src.get("media_type", "image/png")))
                    elif src.get("type") == "url":
                        parts.append(ir.ImagePart(url=src.get("url")))
                elif btype == "tool_use":
                    parts.append(ir.ToolUsePart(id=b.get("id", ""), name=b.get("name", ""),
                                                args=b.get("input") or {}))
                elif btype == "tool_result":
                    c = b.get("content")
                    if isinstance(c, str):
                        text = c
                    elif isinstance(c, list):
                        texts = [blk.get("text", "") for blk in c
                                 if isinstance(blk, dict) and blk.get("type") == "text"]
                        joined = " ".join(t for t in texts if t)
                        if joined:
                            text = joined
                        else:
                            # keep non-text blocks, never base64 image blobs
                            others = [b for b in c if isinstance(b, dict)
                                      and b.get("type") not in ("image", "input_image")]
                            text = json.dumps(others) if others else ""
                    elif c is None:
                        text = ""
                    else:
                        text = json.dumps(c)
                    parts.append(ir.ToolResultPart(tool_use_id=b.get("tool_use_id", ""),
                                                   content=text,
                                                   is_error=bool(b.get("is_error")),
                                                   cache_control=b.get("cache_control")))
                elif btype == "thinking":
                    parts.append(ir.ThinkingPart(b.get("thinking", ""),
                                                 b.get("signature")))
        if parts:
            messages.append(ir.Message(role="assistant" if role == "assistant" else "user",
                                       parts=parts))

    tools = [
        ir.Tool(name=t.get("name", ""), description=t.get("description", ""),
                parameters_json_schema=t.get("input_schema") or {"type": "object"})
        for t in body.get("tools") or []
    ]
    tc_raw = body.get("tool_choice") or {}
    tool_choice: ir.ToolChoice | None = None
    if isinstance(tc_raw, dict):
        if tc_raw.get("type") == "any":
            tool_choice = ir.ToolChoiceRequired()
        elif tc_raw.get("type") == "tool":
            tool_choice = ir.ToolChoiceNamed(tc_raw.get("name", ""))

    thinking = body.get("thinking") or {}
    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=body.get("max_tokens"),
        stop=list(body.get("stop_sequences") or []),
        thinking_budget=(thinking.get("budget_tokens")
                         if thinking.get("type") == "enabled" else None),
    )
    return ir.Request(model=body["model"], messages=messages, tools=tools,
                      tool_choice=tool_choice, gen_params=g,
                      stream=bool(body.get("stream")))


def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for t in turn.thinking:
        tb: dict[str, Any] = {"type": "thinking", "thinking": t.text}
        if t.signature:
            tb["signature"] = t.signature
        content.append(tb)
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for t in turn.tool_calls:
        content.append({"type": "tool_use", "id": t.id, "name": t.name,
                        "input": t.args})
    if not content:
        content = [{"type": "text", "text": ""}]
    u = turn.usage
    sr = {"stop": "end_turn", "length": "max_tokens", "tool_call": "tool_use",
          "content_filter": "refusal"}.get(turn.stop_reason, "end_turn")
    return {
        "id": f"msg_{req_id}", "type": "message", "role": "assistant",
        "model": model, "content": content,
        "stop_reason": sr, "stop_sequence": None,
        "usage": {
            "input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
            "cache_read_input_tokens": u.cached_tokens,
            "cache_creation_input_tokens": u.cache_creation_tokens,
            "output_tokens_details": {
                "thinking_tokens": u.reasoning_tokens,
            } if u.reasoning_tokens else {},
        },
    }


class AnthropicStreamEncoder:
    """IR deltas -> Anthropic SSE events (docs/CORE.md §7.2 fsm_anthropic)."""

    def __init__(self, model: str, req_id: str):
        self.model = model
        self.req_id = req_id
        self._block_idx = 0
        self._open_block: str | None = None  # "text" | "thinking" | "tool:<idx>"
        # Signature seen while no thinking block is open (cross-provider quirk);
        # flushed into the next thinking block right before it closes.
        self._pending_sig: str | None = None
        self._usage: dl.UsageFinal | None = None
        self._stop = "end_turn"
        self._started = False

    def _evt(self, event: str, payload: dict[str, Any]) -> bytes:
        return sse_frame(event, orjson.dumps(payload).decode())

    def _close_block(self) -> list[bytes]:
        if self._open_block is None:
            return []
        idx = self._block_idx - 1
        kind = self._open_block
        self._open_block = None
        out: list[bytes] = []
        if kind == "thinking" and self._pending_sig:
            out.append(self._evt("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "signature_delta", "signature": self._pending_sig}}))
            self._pending_sig = None
        out.append(self._evt("content_block_stop",
                             {"type": "content_block_stop", "index": idx}))
        return out

    def feed(self, d: dl.IRStreamDelta) -> bytes | None:
        if isinstance(d, dl.StreamStart):
            self._started = True
            return self._evt("message_start", {
                "type": "message_start",
                "message": {"id": f"msg_{self.req_id}", "type": "message",
                            "role": "assistant", "model": self.model, "content": [],
                            "stop_reason": None, "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0}}})
        if isinstance(d, dl.TextDelta):
            out = []
            if self._open_block != "text":
                out.extend(self._close_block())
                out.append(self._evt("content_block_start", {
                    "type": "content_block_start", "index": self._block_idx,
                    "content_block": {"type": "text", "text": ""}}))
                self._open_block = "text"
                self._block_idx += 1
            out.append(self._evt("content_block_delta", {
                "type": "content_block_delta", "index": self._block_idx - 1,
                "delta": {"type": "text_delta", "text": d.text}}))
            return b"".join(out)
        if isinstance(d, dl.ThinkingDelta):
            if not d.text and d.signature:
                if self._open_block == "thinking":
                    return self._evt("content_block_delta", {
                        "type": "content_block_delta",
                        "index": self._block_idx - 1,
                        "delta": {"type": "signature_delta", "signature": d.signature}})
                # No thinking block open: buffer instead of stamping a signature
                # onto a nonexistent (or wrong-type) block.
                self._pending_sig = d.signature
                return None
            out = []
            if self._open_block != "thinking":
                out.extend(self._close_block())
                out.append(self._evt("content_block_start", {
                    "type": "content_block_start", "index": self._block_idx,
                    "content_block": {"type": "thinking", "thinking": ""}}))
                self._open_block = "thinking"
                self._block_idx += 1
            out.append(self._evt("content_block_delta", {
                "type": "content_block_delta", "index": self._block_idx - 1,
                "delta": {"type": "thinking_delta", "thinking": d.text}}))
            # Preserve signature if both text and signature arrived together
            if d.signature:
                self._pending_sig = d.signature
            return b"".join(out)
        if isinstance(d, dl.ToolCallOpen):
            out = self._close_block()
            out.append(self._evt("content_block_start", {
                "type": "content_block_start", "index": self._block_idx,
                "content_block": {"type": "tool_use", "id": d.id, "name": d.name,
                                  "input": {}}}))
            self._open_block = f"tool:{self._block_idx}"
            self._block_idx += 1
            return b"".join(out)
        if isinstance(d, dl.ToolCallArgsDelta):
            idx = int(self._open_block.split(":")[1]) if self._open_block else 0
            return self._evt("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": d.args_fragment}})
        if isinstance(d, dl.ToolCallClose):
            return b"".join(self._close_block())
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None
        if isinstance(d, dl.Finish):
            self._stop = {"stop": "end_turn", "length": "max_tokens",
                          "tool_call": "tool_use",
                          "content_filter": "refusal"}.get(d.stop_reason, "end_turn")
            return None
        if isinstance(d, dl.StreamEnd):
            return None  # caller emits message_delta (final_frame) then message_stop
        if isinstance(d, dl.StreamError):
            return self._evt("error", {"type": "error",
                                       "error": {"type": "api_error",
                                                 "message": d.message}})
        return None

    def final_frame(self) -> bytes:
        # A legal stream always closes the currently open content block before
        # the terminating message_delta, even when the last delta left one open.
        out = b"".join(self._close_block())
        u = self._usage or dl.UsageFinal()
        return out + self._evt("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self._stop, "stop_sequence": None},
            "usage": {"output_tokens": u.output,
                      "input_tokens": u.prompt,
                      "cache_read_input_tokens": u.cached,
                      "cache_creation_input_tokens": u.cache_creation}})


def error_body(status: int, etype: str, message: str) -> dict[str, Any]:
    amap = {"authentication_error": "authentication_error",
            "permission_error": "permission_error",
            "rate_limit_error": "rate_limit_error",
            "invalid_request_error": "invalid_request_error",
            "not_found_error": "not_found_error",
            "api_error": "api_error",
            "service_unavailable": "overloaded_error",
            "timeout": "api_error",
            "budget_exceeded": "permission_error",
            "context_window_exceeded": "invalid_request_error",
            "content_policy_violation": "invalid_request_error"}
    return {"type": "error",
            "error": {"type": amap.get(etype, "api_error"), "message": message}}
