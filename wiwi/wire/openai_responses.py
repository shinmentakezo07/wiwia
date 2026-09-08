"""OpenAI Responses wire codec: /v1/responses decode + stream events (fsm_responses).

Stateless mode: every request is self-contained (Codex sends full history with
store:false). previous_response_id is rejected with a clear error (post-MVP).
"""

from __future__ import annotations

import json
import time
from typing import Any

import orjson
import structlog

from wiwi.core.context import RequestContext
from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.streaming import deltas as dl
from wiwi.streaming.partial_json import _repair_truncated_json
from wiwi.streaming.sse import sse_frame
from wiwi.wire.openai_chat import DialectError

log = structlog.get_logger()


def _load_args(raw_args: str) -> dict[str, Any]:
    """Parse tool-call arguments, repairing a truncated JSON string.

    A client replaying history may send arguments the upstream stream never
    closed; parse them rather than dropping the whole call.
    """
    try:
        return json.loads(raw_args)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_truncated_json(raw_args))
    except json.JSONDecodeError:
        return {}


def _decode_image(url: Any) -> ir.ImagePart | None:
    """Parse an image reference, accepting a data URL or a remote URL.

    Returns None (rather than raising) for a non-string value — a Chat-style
    ``image_url`` dict, a list, or ``None`` — so the caller skips the block
    instead of 500-ing on ``url.startswith``. Matches the codec policy that
    every other malformed block already follows (skip, don't crash).
    """
    if not isinstance(url, str):
        return None
    if url.startswith("data:"):
        header, _, b64 = url.partition(",")
        mime = header[5:].split(";")[0] or "image/png"
        return ir.ImagePart(b64=b64, mime=mime)
    return ir.ImagePart(url=url)


def _decode_document(c: dict[str, Any]) -> ir.DocumentPart | None:
    """Parse an ``input_file`` content block into a DocumentPart."""
    raw = c.get("file_data") or c.get("file_url") or ""
    if not isinstance(raw, str) or not raw:
        return None  # non-string file ref: skip rather than 500
    if raw.startswith("data:"):
        header, _, b64 = raw.partition(",")
        return ir.DocumentPart(b64=b64,
                               mime=header[5:].split(";")[0] or "application/pdf",
                               name=c.get("filename"))
    return ir.DocumentPart(url=raw, name=c.get("filename"))


def _item_text(item: dict[str, Any]) -> str:
    """Flatten a function_call_output ``output`` into a single string.

    Accepts a plain string, a list of output_text blocks, or any other
    JSON-serializable payload.
    """
    output = item.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return " ".join(c.get("text", "") for c in output
                        if isinstance(c, dict)
                        and c.get("type") in ("output_text", "text")) \
            or json.dumps(output)
    if output is None:
        return ""
    return json.dumps(output)


def _item_images(item: dict[str, Any]) -> list[ir.ImagePart]:
    """Collect image blocks in a function_call_output (screenshots, etc.).

    Mirrors openai_chat's tool-image handling so multimodal tool results keep
    their images on this surface too.
    """
    output = item.get("output")
    if not isinstance(output, list):
        return []
    images: list[ir.ImagePart] = []
    for c in output:
        if not isinstance(c, dict):
            continue
        if c.get("type") not in ("input_image", "image", "computer_screenshot"):
            continue
        url = c.get("image_url") or c.get("image") or c.get("file_data") or ""
        img = _decode_image(url)
        if img is not None:
            images.append(img)
    return images


def _decode_tool(t: dict[str, Any]) -> ir.Tool:
    """Map one Responses tool entry onto an IR Tool."""
    ttype = t.get("type")
    if ttype == "function":
        return ir.Tool(name=t.get("name", ""),
                       description=t.get("description", ""),
                       parameters_json_schema=t.get("parameters") or {"type": "object"},
                       strict=t.get("strict"))
    canonical = bt.canonical_for("openai_responses", ttype)
    if canonical is not None:
        # Hosted builtin (web_search family). OpenAI nests domain filters
        # under "filters"; normalize to the flat config subset.
        config: dict[str, Any] = {k: t[k] for k in bt.BUILTIN_CONFIG_KEYS if k in t}
        filters = t.get("filters") if isinstance(t.get("filters"), dict) else {}
        if "allowed_domains" in filters:
            config["allowed_domains"] = filters["allowed_domains"]
        if "blocked_domains" in filters:
            config["blocked_domains"] = filters["blocked_domains"]
        return ir.Tool(name=canonical, builtin=canonical, builtin_config=config)
    # Unknown hosted tool (file_search, computer, ...): keep it
    # builtin-shaped so no surface mangles it into a function tool;
    # providers that can't host it drop it with a warning.
    return ir.Tool(name=t.get("name") or ttype, builtin=ttype,
                   builtin_config={bt.WIRE_TYPE_KEY: ttype})


def _decode_tool_choice(tc_raw: Any) -> ir.ToolChoice | None:
    if tc_raw == "auto":
        return ir.ToolChoiceAuto()
    if tc_raw == "none":
        return ir.ToolChoiceNone()
    if tc_raw == "required":
        return ir.ToolChoiceRequired()
    if isinstance(tc_raw, dict):
        ttype = tc_raw.get("type")
        if ttype == "function":
            return ir.ToolChoiceNamed(tc_raw.get("name", ""))
        if ttype == "allowed_tools":
            # 2025 form: {"type": "allowed_tools", "mode": ..., "tools": [...]}.
            # Responses has no allowed-list concept in the IR; keep the mode.
            return (ir.ToolChoiceRequired() if tc_raw.get("mode") == "required"
                    else ir.ToolChoiceAuto())
        if isinstance(ttype, str) and ttype:
            # Named HOSTED tool choice ({"type": "web_search"}): Responses
            # allows pinning a provider-hosted tool, and every adapter renders
            # ToolChoiceNamed, so preserve it instead of dropping to None.
            return ir.ToolChoiceNamed(ttype)
    return None


def _decode_response_format(text_field: Any) -> ir.ResponseFormat | None:
    """Responses nests structured-output config under ``text.format``."""
    if not isinstance(text_field, dict):
        return None
    fmt = text_field.get("format") or {}
    if fmt.get("type") == "json_schema":
        return ir.ResponseFormat(type="json_schema", json_schema=fmt.get("schema"),
                                 name=fmt.get("name"), strict=fmt.get("strict"))
    if fmt.get("type") == "json_object":
        return ir.ResponseFormat(type="json_object")
    return None


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
        if not isinstance(item, dict):
            # Malformed item: skip rather than 500 on .get (same policy as the
            # chat and anthropic codecs).
            continue
        itype = item.get("type", "message")
        if itype == "message":
            role = item.get("role", "user")
            content = item.get("content")
            parts: list[ir.Part] = []
            if isinstance(content, str):
                parts.append(ir.TextPart(content))
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue  # malformed block: skip rather than 500 on .get
                    ctype = c.get("type", "output_text" if role == "assistant" else "input_text")
                    if ctype in ("input_text", "output_text", "text"):
                        parts.append(ir.TextPart(c.get("text", "")))
                    elif ctype in ("input_image", "image"):
                        img = _decode_image(c.get("image_url") or "")
                        if img is not None:
                            parts.append(img)
                    elif ctype == "input_file":
                        doc = _decode_document(c)
                        if doc is not None:
                            parts.append(doc)
                    elif ctype == "input_audio":
                        ia = c.get("input_audio") or {}
                        data = ia.get("data") if isinstance(ia, dict) else None
                        if isinstance(data, str) and data:
                            parts.append(ir.AudioPart(
                                b64=data,
                                mime=f"audio/{ia.get('format') or 'wav'}"))
                        else:
                            log.debug("responses_unsupported_content_block",
                                      block_type=ctype)
                    else:
                        log.debug("responses_unsupported_content_block",
                                  block_type=ctype)
            ir_role = ("system" if role in ("system", "developer")
                       else "assistant" if role == "assistant" else "user")
            messages.append(ir.Message(role=ir_role, parts=parts))
        elif itype == "function_call":
            raw_args = item.get("arguments") or "{}"
            messages.append(ir.Message(role="assistant", parts=[
                ir.ToolUsePart(id=item.get("call_id", ""), name=item.get("name", ""),
                               args=_load_args(raw_args), raw_args=raw_args)]))
        elif itype == "function_call_output":
            # Images in a tool result (computer-use screenshots) ride
            # ToolResultPart.images so multimodal adapters can re-emit them.
            messages.append(ir.Message(role="tool", parts=[
                ir.ToolResultPart(tool_use_id=item.get("call_id", ""),
                                  content=_item_text(item),
                                  images=_item_images(item))]))
        elif itype == "reasoning":
            summary = item.get("summary") or []
            text = " ".join(s.get("text", "") for s in summary if isinstance(s, dict))
            if text:
                messages.append(ir.Message(role="assistant",
                                           parts=[ir.ThinkingPart(text)]))
        else:
            # Unsupported item (computer_call, mcp_call, item_reference, ...).
            # Warn rather than reject: a client that currently gets partial
            # results keeps working, and the loss is visible in the logs.
            log.warning("responses_unsupported_input_item", item_type=itype)

    tools = [_decode_tool(t) for t in body.get("tools") or [] if isinstance(t, dict)]
    tool_choice = _decode_tool_choice(body.get("tool_choice"))

    stop_raw = body.get("stop")
    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=(body.get("max_output_tokens")),
        # stop is not a documented Responses param, but clients that send it
        # mean the same thing; accept a bare string or a list.
        stop=[stop_raw] if isinstance(stop_raw, str) else (stop_raw or []),
        seed=body.get("seed"),
        top_k=body.get("top_k"),
        parallel_tool_calls=body.get("parallel_tool_calls"),
        disable_parallel_tool_use=(True if body.get("parallel_tool_calls") is False else None),
        reasoning_effort=((body.get("reasoning") or {}).get("effort")
                          if isinstance(body.get("reasoning"), dict) else None),
    )
    g.response_format = _decode_response_format(body.get("text"))
    return ir.Request(model=model, messages=messages, tools=tools,
                      tool_choice=tool_choice, gen_params=g,
                      stream=bool(body.get("stream")),
                      # Unmapped Responses params (prompt_cache_key,
                      # safety_identifier, store, metadata, truncation, ...):
                      # openai_adapter forwards these upstream, so keep them
                      # rather than dropping them on the floor.
                      extras={k: v for k, v in body.items()
                              if k not in _KNOWN_KEYS})


def _usage_obj(prompt: int, output: int, cached: int, reasoning: int) -> dict[str, Any]:
    """OpenAI Responses usage block — shared by the sync and stream paths."""
    return {
        "input_tokens": prompt, "output_tokens": output,
        "total_tokens": prompt + output,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens_details": {"reasoning_tokens": reasoning},
    }


# stop_reason -> Responses incomplete_details.reason. A safety block is NOT a
# successful completion; reporting "completed" would hide it from the client.
_INCOMPLETE_REASONS = {"length": "max_output_tokens", "content_filter": "content_filter"}


def _response_obj(model: str, req_id: str, stop: str, output: list[dict[str, Any]],
                  usage: dict[str, Any], created_at: int | None = None) -> dict[str, Any]:
    """Terminal response object, shared by encode_response and _completed().

    Truncated or filtered output is a distinct status ("incomplete" +
    incomplete_details), mirroring the streaming path's response.incomplete
    terminal event. ``created_at`` is sync-path only (the stream's terminal
    event omits it).
    """
    resp: dict[str, Any] = {"id": f"resp_{req_id}", "object": "response"}
    if created_at is not None:
        resp["created_at"] = created_at
    reason = _INCOMPLETE_REASONS.get(stop)
    resp.update({
        "status": ("incomplete" if reason else "completed"),
        "model": model, "output": output, "usage": usage,
    })
    if reason:
        resp["incomplete_details"] = {"reason": reason}
    return resp


# --- output item builders (shared by encode_response and the stream encoder) --

def _reasoning_item(text: str, req_id: str, n: int) -> dict[str, Any]:
    """A reasoning output item; ``n`` disambiguates multiple thinking blocks."""
    return {"type": "reasoning", "id": f"rs_{req_id}_{n}",
            "summary": [{"type": "summary_text", "text": text}]}


def _message_item(text: str, req_id: str) -> dict[str, Any]:
    return {"type": "message", "id": f"msg_{req_id}", "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _function_call_item(item_id: str, call_id: str, name: str,
                        arguments: str) -> dict[str, Any]:
    return {"type": "function_call", "id": item_id, "call_id": call_id,
            "name": name, "arguments": arguments}


def _builtin_call_item(item_id: str, query: str) -> dict[str, Any]:
    """A1 carve-out: a hosted builtin call is a self-contained web_search_call.

    The Responses protocol has no separate result item for hosted tools, so
    replay needs no pairing. Takes the query already extracted — the sync path
    reads a parsed args dict while the stream path parses a raw JSON string,
    and the two are not interchangeable (see _builtin_query).
    """
    return {"type": "web_search_call", "id": item_id, "status": "completed",
            "action": {"type": "search", "query": query}}


def _builtin_query(arguments: str) -> str:
    """Extract the search query from a hosted call's raw argument string."""
    try:
        args = orjson.loads(arguments) if arguments else {}
    except orjson.JSONDecodeError:
        args = {}
    return args.get("query", "")


# Params decode_request maps onto the IR; everything else rides Request.extras
# so adapters that forward standard OpenAI fields still see them.
_KNOWN_KEYS = {
    "model", "input", "instructions", "previous_response_id", "tools",
    "tool_choice", "parallel_tool_calls", "reasoning", "text", "stream",
    "temperature", "top_p", "max_output_tokens", "stop", "seed", "top_k",
}


def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    # One counter across thinking/message/tool items: a function_call's item id
    # depends on how many items preceded it, so enumerate() over each list
    # separately would renumber ids and break multi-turn replay.
    out_id = 0
    for t in turn.thinking:
        output.append(_reasoning_item(t.text, req_id, out_id))
        out_id += 1
    if turn.text or not turn.tool_calls:
        output.append(_message_item(turn.text, req_id))
        out_id += 1
    for t in turn.tool_calls:
        if bt.is_builtin_name(t.name):
            # Read the parsed dict, not raw_args: providers set both, but a
            # raw_args that failed to parse must not blank an available query.
            output.append(_builtin_call_item(t.id, t.args.get("query", "")))
        else:
            output.append(_function_call_item(f"fc_{req_id}_{out_id}", t.id, t.name,
                                              t.raw_args or json.dumps(t.args)))
        out_id += 1
    u = turn.usage
    return _response_obj(
        model, req_id, turn.stop_reason, output,
        _usage_obj(u.prompt_tokens, u.completion_tokens, u.cached_tokens,
                   u.reasoning_tokens),
        created_at=int(time.time()))


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

    def _item_done(self, idx: int, item: dict[str, Any]) -> bytes:
        """output_item.done — the one event every closed item has in common."""
        self._output.append(item)
        return self._evt("response.output_item.done",
                         {"output_index": idx, "item": item})

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
        if t.get("builtin"):
            # Stream path has only the accumulated raw argument string.
            item = _builtin_call_item(t["call_id"] or f"ws_{self.req_id}_{n}",
                                      _builtin_query(t["args"]))
            return [self._item_done(idx, item)]
        item_id = f"fc_{self.req_id}_{n}"
        item = _function_call_item(item_id, t["call_id"], t["name"], t["args"])
        return [self._evt("response.function_call_arguments.done", {
                    "item_id": item_id, "output_index": idx,
                    "arguments": t["args"]}),
                self._item_done(idx, item)]

    def _close_item(self) -> list[bytes]:
        if self._item_open is None:
            return []
        kind = self._item_open
        idx = self._open_out
        self._item_open = None
        if kind == "message":
            text = "".join(self._text_buf)
            item_id = f"msg_{self.req_id}"
            part = {"type": "output_text", "text": text, "annotations": []}
            return [self._evt("response.output_text.done", {
                        "item_id": item_id, "output_index": idx,
                        "content_index": 0, "text": text}),
                    self._evt("response.content_part.done", {
                        "item_id": item_id, "output_index": idx,
                        "content_index": 0, "part": part}),
                    self._item_done(idx, _message_item(text, self.req_id))]
        if kind == "thinking":
            think = "".join(self._think_buf)
            item = _reasoning_item(think, self.req_id, idx)
            return [self._evt("response.reasoning_summary_text.done", {
                        "item_id": item["id"], "output_index": idx,
                        "text": think}),
                    self._item_done(idx, item)]
        # kind == "tool". Delegate to _close_tool, which POPS the entry from
        # self._tools: reading it in place would leave the tool recorded as
        # open, so the tool's own ToolCallClose later re-emits a second
        # output_item.done at the same output_index — Codex CLI then counts a
        # phantom tool call.
        return self._close_tool(self._open_tool)

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
                item_id = f"rs_{self.req_id}_{oi}"
                out.append(self._evt("response.output_item.added", {
                    "output_index": oi,
                    "item": {"type": "reasoning", "id": item_id, "summary": []}}))
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
                              "call_id": d.id, "args": "", "output_index": oi,
                              "builtin": d.builtin}
            self._open_tool = n
            # A1 carve-out: hosted builtin calls open as self-contained
            # web_search_call items (replay-safe: no separate result item
            # exists in this protocol).
            if d.builtin is not None:
                item: dict[str, Any] = {"type": "web_search_call",
                                        "id": d.id or f"ws_{self.req_id}_{n}",
                                        "status": "in_progress",
                                        "action": {"type": "search", "query": ""}}
            else:
                item = _function_call_item(f"fc_{self.req_id}_{n}", d.id, d.name, "")
            out.append(self._evt("response.output_item.added", {
                "output_index": oi, "item": item}))
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
        resp = _response_obj(self.model, self.req_id, self._stop, self._output,
                             _usage_obj(u.prompt, u.output, u.cached, u.reasoning))
        return closing + self._evt(etype, {"response": resp})


def error_body(status: int, etype: str, message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": etype, "code": etype,
                      "param": None}}
