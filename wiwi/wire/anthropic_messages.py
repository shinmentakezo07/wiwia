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
                if not isinstance(b, dict):
                    continue  # malformed block: skip rather than 500 on .get
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
                    elif src.get("type") == "file":
                        parts.append(ir.ImagePart(file_id=src.get("file_id")))
                elif btype == "document":
                    src = b.get("source") or {}
                    if src.get("type") == "base64":
                        parts.append(ir.DocumentPart(
                            b64=src.get("data"),
                            mime=src.get("media_type", "application/pdf"),
                            name=b.get("title"), context=b.get("context")))
                    elif src.get("type") == "url":
                        parts.append(ir.DocumentPart(
                            url=src.get("url"), name=b.get("title"),
                            context=b.get("context")))
                elif btype == "tool_use":
                    parts.append(ir.ToolUsePart(id=b.get("id", ""), name=b.get("name", ""),
                                                args=b.get("input") or {}))
                elif btype == "server_tool_use":
                    # Server-side tools (web_search, code_execution, mcp, ...):
                    # treat as a plain tool use so echoed history keeps the
                    # turn and its paired *_tool_result stays balanced.
                    parts.append(ir.ToolUsePart(id=b.get("id", ""), name=b.get("name", ""),
                                                args=b.get("input") or {}))
                elif btype == "tool_result" or btype.endswith("_tool_result"):
                    # Covers user tool_result AND the server-tool result
                    # family (web_search_tool_result, code_execution_tool_result,
                    # mcp_tool_result, computer_tool_result, browser_tool_result).
                    c = b.get("content")
                    images: list[ir.ImagePart] = []
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
                        for blk in c:
                            # Multimodal tool results: collect image blocks
                            # (base64/url/file sources) so providers with
                            # native image support can re-emit them.
                            if not isinstance(blk, dict) or blk.get("type") != "image":
                                continue
                            src = blk.get("source") or {}
                            if src.get("type") == "base64":
                                images.append(ir.ImagePart(
                                    b64=src.get("data"),
                                    mime=src.get("media_type", "image/png")))
                            elif src.get("type") == "url":
                                images.append(ir.ImagePart(url=src.get("url")))
                            elif src.get("type") == "file":
                                images.append(ir.ImagePart(file_id=src.get("file_id")))
                    elif c is None:
                        text = ""
                    else:
                        text = json.dumps(c)
                    parts.append(ir.ToolResultPart(tool_use_id=b.get("tool_use_id", ""),
                                                   content=text,
                                                   is_error=bool(b.get("is_error")),
                                                   cache_control=b.get("cache_control"),
                                                   images=images))
                elif btype == "thinking":
                    parts.append(ir.ThinkingPart(b.get("thinking", ""),
                                                 b.get("signature")))
        if parts:
            messages.append(ir.Message(role="assistant" if role == "assistant" else "user",
                                       parts=parts))

    tools = [
        ir.Tool(name=t.get("name", ""), description=t.get("description", ""),
                parameters_json_schema=t.get("input_schema") or {"type": "object"},
                strict=t.get("strict"),
                input_examples=t.get("input_examples"),
                cache_control=t.get("cache_control"))
        for t in body.get("tools") or []
    ]
    tc_raw = body.get("tool_choice") or {}
    tool_choice: ir.ToolChoice | None = None
    disable_parallel: bool | None = None
    if isinstance(tc_raw, dict):
        disable_parallel = tc_raw.get("disable_parallel_tool_use")
        tc_type = tc_raw.get("type")
        if tc_type == "any":
            tool_choice = ir.ToolChoiceRequired()
        elif tc_type == "tool":
            tool_choice = ir.ToolChoiceNamed(tc_raw.get("name", ""))
        elif tc_type == "auto":
            tool_choice = ir.ToolChoiceAuto()
        elif tc_type == "none":
            tool_choice = ir.ToolChoiceNone()

    thinking = body.get("thinking")
    if not isinstance(thinking, dict):
        thinking = {}  # malformed (e.g. a string): ignore rather than crash
    # Thinking modes (2026): enabled carries budget_tokens; adaptive is
    # model-driven (no budget); disabled turns thinking off and also maps to
    # reasoning_effort="none" so OpenAI upstreams disable reasoning.
    ttype = thinking.get("type")
    thinking_type = ttype if ttype in ("enabled", "adaptive", "disabled") else None
    reasoning_effort = "none" if thinking_type == "disabled" else None
    # Structured outputs GA: output_config.format is the native json_schema
    # carrier (analogous to OpenAI's response_format.json_schema).
    response_format: ir.ResponseFormat | None = None
    oc_fmt = ((body.get("output_config") or {}).get("format")
              if isinstance(body.get("output_config"), dict) else None)
    if isinstance(oc_fmt, dict) and oc_fmt.get("type") == "json_schema":
        response_format = ir.ResponseFormat(
            type="json_schema", json_schema=oc_fmt.get("schema"),
            name=oc_fmt.get("name"), strict=oc_fmt.get("strict"))
    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=body.get("max_tokens"),
        stop=list(body.get("stop_sequences") or []),
        thinking_budget=(thinking.get("budget_tokens")
                         if thinking_type == "enabled" else None),
        thinking_type=thinking_type,
        reasoning_effort=reasoning_effort,
        top_k=body.get("top_k") if isinstance(body.get("top_k"), int) else None,
        disable_parallel_tool_use=disable_parallel,
        response_format=response_format,
    )
    return ir.Request(model=body["model"], messages=messages, tools=tools,
                      tool_choice=tool_choice, gen_params=g,
                      stream=bool(body.get("stream")),
                      extras={k: v for k, v in body.items()
                              if k in _PASSTHROUGH_KEYS})


# 2026 Anthropic top-level params the IR doesn't model as GenParams fields.
# Known-safe to forward to an Anthropic upstream verbatim; other adapters
# ignore extras they don't understand (subject to their drop_params policy).
_PASSTHROUGH_KEYS = {
    "service_tier", "speed", "metadata", "mcp_servers", "container",
    "context_management", "fallbacks", "cache_control",
}


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
        "stop_reason": sr, "stop_sequence": turn.stop_sequence,
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
        self._open_block: str | None = None  # "text" | "thinking" | "tool"
        # Map from IR tool index to Anthropic content block index, so
        # interleaved parallel tool calls route args to the right block.
        self._tool_blocks: dict[int, int] = {}
        self._open_tool: int | None = None
        # Signature seen while no thinking block is open (cross-provider quirk);
        # flushed into the next thinking block right before it closes.
        self._pending_sig: str | None = None
        # Block index of the most recent thinking block, so a late pending
        # signature stamps the RIGHT block — never a later thinking block.
        self._last_think_idx: int | None = None
        self._usage: dl.UsageFinal | None = None
        self._stop = "end_turn"
        self._stop_seq: str | None = None
        self._started = False
        # Per-delta skeleton, allocated once: only `index` and the delta body
        # change between consecutive deltas of the same kind.
        self._text_delta: dict[str, Any] = {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": ""}}
        self._think_delta: dict[str, Any] = {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": ""}}
        self._sig_delta: dict[str, Any] = {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "signature_delta", "signature": ""}}
        self._json_delta: dict[str, Any] = {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": ""}}

    def _evt(self, event: str, payload: dict[str, Any]) -> bytes:
        return sse_frame(event, orjson.dumps(payload).decode())

    def _close_block(self, tool_index: int | None = None) -> list[bytes]:
        """Close the currently open block, or a specific tool by IR index."""
        if tool_index is not None:
            # Close a specific tool's content block (parallel-safe).
            idx = self._tool_blocks.pop(tool_index, None)
            if idx is None:
                return []
            if self._open_tool == tool_index:
                self._open_block = None
                self._open_tool = None
            return [self._evt("content_block_stop",
                              {"type": "content_block_stop", "index": idx})]
        if self._open_block is None:
            return []
        idx = self._block_idx - 1
        kind = self._open_block
        if kind == "tool" and self._open_tool is not None:
            idx = self._tool_blocks.get(self._open_tool, self._block_idx - 1)
            self._tool_blocks.pop(self._open_tool, None)
            self._open_tool = None
        self._open_block = None
        out: list[bytes] = []
        if kind == "thinking":
            self._last_think_idx = idx
            if self._pending_sig:
                out.append(self._evt("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "signature_delta",
                              "signature": self._pending_sig}}))
                self._pending_sig = None
        out.append(self._evt("content_block_stop",
                             {"type": "content_block_stop", "index": idx}))
        return out

    def _flush_pending_sig(self) -> list[bytes]:
        """Emit a pending signature as a late delta against the last thinking
        block. Fires when a NEW thinking block opens while one is pending (the
        signature belongs to the earlier block) and at final_frame (otherwise it
        would be dropped). A late delta on a stopped block is tolerated by the
        Anthropic SDK and Claude Code — deltas dispatch by index — and beats
        dropping the signature, which hard-400s the next turn's thinking replay."""
        if not self._pending_sig or self._last_think_idx is None:
            return []
        sd = self._sig_delta
        sd["index"] = self._last_think_idx
        sd["delta"]["signature"] = self._pending_sig
        self._pending_sig = None
        return [self._evt("content_block_delta", sd)]

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
            # A text delta can never be emitted while a tool_use block is
            # open: Anthropic content blocks are strictly sequential, and
            # closing the tool block would lose its index mapping so a later
            # ToolCallArgsDelta would land on the text block (Claude Code
            # rejects "Content block is not a input_json block"). Interleaved
            # text is suppressed; the tool's args keep streaming legally.
            if self._open_block == "tool":
                return None
            out = []
            if self._open_block != "text":
                out.extend(self._close_block())
                out.append(self._evt("content_block_start", {
                    "type": "content_block_start", "index": self._block_idx,
                    "content_block": {"type": "text", "text": ""}}))
                self._open_block = "text"
                self._block_idx += 1
            td = self._text_delta
            td["index"] = self._block_idx - 1
            td["delta"]["text"] = d.text
            out.append(self._evt("content_block_delta", td))
            return b"".join(out)
        if isinstance(d, dl.ThinkingDelta):
            if not d.text and d.signature:
                if self._open_block == "thinking":
                    sd = self._sig_delta
                    sd["index"] = self._block_idx - 1
                    sd["delta"]["signature"] = d.signature
                    return self._evt("content_block_delta", sd)
                # No thinking block open: buffer instead of stamping a signature
                # onto a nonexistent (or wrong-type) block.
                self._pending_sig = d.signature
                return None
            # Thinking with text while a tool block is open: suppress it so a
            # later ToolCallArgsDelta keeps routing to the tool block.
            if self._open_block == "tool":
                if d.signature:
                    self._pending_sig = d.signature
                return None
            out = []
            if self._open_block != "thinking":
                out.extend(self._close_block())
                # A signature pending from a PREVIOUS thinking block belongs to
                # that block, not this new one.
                out.extend(self._flush_pending_sig())
                out.append(self._evt("content_block_start", {
                    "type": "content_block_start", "index": self._block_idx,
                    "content_block": {"type": "thinking", "thinking": ""}}))
                self._open_block = "thinking"
                self._block_idx += 1
            thd = self._think_delta
            thd["index"] = self._block_idx - 1
            thd["delta"]["thinking"] = d.text
            out.append(self._evt("content_block_delta", thd))
            # Preserve signature if both text and signature arrived together
            if d.signature:
                self._pending_sig = d.signature
            return b"".join(out)
        if isinstance(d, dl.ToolCallOpen):
            out: list[bytes] = []
            # Only close the open block if it's text/thinking — parallel
            # tool calls are siblings, not sequential.
            if self._open_block is not None and self._open_block != "tool":
                out.extend(self._close_block())
            # `id` must be a string (Claude Code rejects "string id" style
            # errors when a provider hands back an integer tool id).
            tool_id = d.id if isinstance(d.id, str) else str(d.id)
            out.append(self._evt("content_block_start", {
                "type": "content_block_start", "index": self._block_idx,
                "content_block": {"type": "tool_use", "id": tool_id, "name": d.name,
                                  "input": {}}}))
            self._tool_blocks[d.index] = self._block_idx
            self._open_tool = d.index
            self._open_block = "tool"
            self._block_idx += 1
            return b"".join(out)
        if isinstance(d, dl.ToolCallArgsDelta):
            idx = self._tool_blocks.get(d.index)
            if idx is None:
                # No open tool_use block for this index. The IR contract
                # guarantees ToolCallOpen precedes its ArgsDelta, so this only
                # fires on a malformed stream. NEVER stamp input_json_delta
                # onto a text/thinking block (Claude Code rejects that), so
                # drop the fragment rather than corrupt the stream.
                return None
            jd = self._json_delta
            jd["index"] = idx
            jd["delta"]["partial_json"] = d.args_fragment
            return self._evt("content_block_delta", jd)
        if isinstance(d, dl.ToolCallClose):
            return b"".join(self._close_block(tool_index=d.index))
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None
        if isinstance(d, dl.Finish):
            self._stop = {"stop": "end_turn", "length": "max_tokens",
                          "tool_call": "tool_use",
                          "content_filter": "refusal"}.get(d.stop_reason, "end_turn")
            self._stop_seq = d.stop_sequence
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
        # A signature still pending here has no later thinking block to ride on:
        # flush it as a late delta against the last thinking block rather than
        # dropping it (a dropped signature hard-400s the next turn's replay).
        out += b"".join(self._flush_pending_sig())
        u = self._usage or dl.UsageFinal()
        return out + self._evt("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self._stop, "stop_sequence": self._stop_seq},
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
