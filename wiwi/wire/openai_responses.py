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
from wiwi.wire.openai_chat import DialectError, _stop_list, _str_or_empty

log = structlog.get_logger()


def _load_args(raw_args: Any) -> dict[str, Any]:
    """Parse tool-call arguments, repairing a truncated JSON string.

    A client replaying history may send arguments the upstream stream never
    closed; parse them rather than dropping the whole call. A JSON *object*
    (which some gateways emit instead of the spec's string) is used directly —
    json.loads would raise TypeError, not JSONDecodeError, and 500 the
    gateway.
    """
    if isinstance(raw_args, dict):
        return raw_args
    try:
        parsed = json.loads(raw_args)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        parsed = json.loads(_repair_truncated_json(raw_args))
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
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
    if not url:
        # An absent or empty `image_url` (the callers pass `c.get(...) or ""`)
        # is not a reference to anything: returning an ImagePart for it sent a
        # 4-byte "image" (`data:image/png;base64,None`) upstream, which the
        # provider rejects with no indication of which block was junk
        # (AUDIT #168). Drop the block instead.
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
    # `name` is typed `str | None` and the fallback estimator appends it to a
    # " ".join: a non-string filename (5, a list) was a 500 there (AUDIT
    # #184). The field is optional, so junk becomes absent rather than "".
    raw_name = c.get("filename")
    name = raw_name if isinstance(raw_name, str) else None
    if raw.startswith("data:"):
        header, _, b64 = raw.partition(",")
        return ir.DocumentPart(b64=b64,
                               mime=header[5:].split(";")[0] or "application/pdf",
                               name=name)
    return ir.DocumentPart(url=raw, name=name)


def _item_text(item: dict[str, Any]) -> str:
    """Flatten a function_call_output ``output`` into a single string.

    Accepts a plain string, a list of output_text blocks, or any other
    JSON-serializable payload.
    """
    output = item.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        # Only string text contributes; a non-string one (7) made " ".join
        # raise TypeError — a 500 for a malformed output block.
        return " ".join(c["text"] for c in output
                        if isinstance(c, dict)
                        and c.get("type") in ("output_text", "text")
                        and isinstance(c.get("text"), str)) \
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
    if not isinstance(ttype, str):
        # A non-string type (a list, a dict) is unhashable: the dict lookups in
        # bt.canonical_for raise TypeError -> 500. Only a string names a tool
        # kind, so treat anything else as an untyped function tool — the same
        # default the type-omitted case already takes.
        ttype = None
    if ttype == "function":
        raw_params = t.get("parameters")
        raw_name = t.get("name")
        raw_desc = t.get("description")
        return ir.Tool(
            # A non-string or explicit-null `name`/`description` reaches the IR
            # typed as str: the null is forwarded upstream, where the provider
            # 400s naming no offending block and the tool stays invisible to
            # the model (AUDIT #186), and the non-string crashes the fallback
            # estimator's " ".join with a 500 (AUDIT #184). `t.get(k, "")`
            # defaults only a MISSING key, so coerce both at the boundary.
            name=_str_or_empty(raw_name),
            description=_str_or_empty(raw_desc),
            # A non-dict schema is otherwise stored verbatim and
            # crashes validate_tool_args inside the stream pump,
            # cooling a healthy deployment for a caller-controlled
            # shape. Same empty-object default as the missing case.
            parameters_json_schema=(raw_params if isinstance(raw_params, dict)
                                    else {"type": "object"}),
            strict=t.get("strict"),
            # Responses defers a function by the same field name the
            # Anthropic surface uses, so it round-trips unchanged.
            defer_loading=t.get("defer_loading"))
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
    # providers that can't host it drop it with a warning. Both operands of
    # `name or ttype` can be a non-string (an explicit null name, or a
    # non-string type that the guard above turned into None), and `Tool.name`
    # is typed str — a null there crashes the fallback estimator's " ".join
    # (AUDIT #184/#186).
    return ir.Tool(name=(_str_or_empty(t.get("name")) or _str_or_empty(ttype)),
                   builtin=ttype,
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
            # A non-string (or explicit-null) name reaches
            # ToolChoiceNamed.name, typed str, and every adapter renders it
            # straight onto the wire (AUDIT #184/#186). Same coercion as a
            # MISSING name, which already lands on "".
            return ir.ToolChoiceNamed(_str_or_empty(tc_raw.get("name")))
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
    fmt = text_field.get("format")
    if not isinstance(fmt, dict):
        # A non-dict ``format`` (e.g. "text") previously reached ``fmt.get``
        # and raised AttributeError, surfacing a trivially malformed body as
        # an unhandled 500 (AUDIT #100). Treat it as unset.
        return None
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
                        # A non-string `text` (5, null, a list) reaches the IR
                        # typed as str and later crashes the fallback
                        # estimator's " ".join — an `internal gateway error`
                        # 500 on the success path, after the upstream was
                        # billed (AUDIT #184). Coerce at the boundary, as
                        # anthropic_messages.py does for its text blocks.
                        parts.append(ir.TextPart(_str_or_empty(c.get("text"))))
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
            raw_args = item.get("arguments")
            if isinstance(raw_args, dict):
                raw_args = json.dumps(raw_args)  # args-as-object gateways
            else:
                if not isinstance(raw_args, str):
                    # A truthy scalar (5, true, ["a"]) is not a JSON argument
                    # string: `raw_args or "{}"` kept it, and `raw_args` is
                    # typed `str | None` — the fallback estimator's " ".join
                    # then raised TypeError (AUDIT #184). Same guard the chat
                    # codec applies to the identical field (AUDIT #124).
                    raw_args = ""
                raw_args = raw_args or "{}"
            messages.append(ir.Message(role="assistant", parts=[
                ir.ToolUsePart(id=item.get("call_id", ""),
                               name=_str_or_empty(item.get("name")),
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
            if not isinstance(summary, list):
                summary = []  # non-list summary: nothing to flatten, skip it
            text = " ".join(s["text"] for s in summary
                            if isinstance(s, dict) and isinstance(s.get("text"), str))
            if text:
                messages.append(ir.Message(role="assistant",
                                           parts=[ir.ThinkingPart(text)]))
        else:
            # Unsupported item (computer_call, mcp_call, item_reference, ...).
            # Warn rather than reject: a client that currently gets partial
            # results keeps working, and the loss is visible in the logs.
            log.warning("responses_unsupported_input_item", item_type=itype)

    raw_tools = body.get("tools")
    if raw_tools is not None and not isinstance(raw_tools, list):
        raise DialectError("'tools' must be a list")  # non-iterable -> 500
    tools = [_decode_tool(t) for t in raw_tools or [] if isinstance(t, dict)]
    tool_choice = _decode_tool_choice(body.get("tool_choice"))

    stop_raw = body.get("stop")
    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=ir.coerce_int(body.get("max_output_tokens")),
        # stop is not a documented Responses param, but clients that send it
        # mean the same thing; accept a bare string or a list. Anything else
        # (true, 7, {"a": 1}) is not a stop sequence — `(stop_raw or [])`
        # forwarded it verbatim and the upstream 400'd naming nothing the
        # caller sent (AUDIT #185).
        stop=_stop_list(stop_raw),
        seed=body.get("seed"),
        # Same class as `max_output_tokens` on the line above: a non-int
        # `top_k` ('7', true, {}) was forwarded upstream verbatim (AUDIT #187).
        # The Chat and Anthropic decoders both coerce their equivalents.
        top_k=ir.coerce_int(body.get("top_k")),
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


def _builtin_is_tool_search(builtin: str | None) -> bool:
    """True when a hosted builtin is a tool search rather than a web search.

    The registry is the source of truth: both ``tool_search_bm25`` and
    ``tool_search_regex`` map onto the Responses surface's generic
    ``tool_search`` wire type, while ``web_search`` maps onto ``web_search``.
    Accepts either the canonical name or the Anthropic wire spelling, since
    ``ToolCallOpen.builtin`` carries whatever the provider named the block.
    """
    if not builtin:
        return False
    canonical = bt.canonical_for("anthropic", builtin) or builtin
    return bt.wire_type_for("openai_responses", canonical) == "tool_search"


def _builtin_call_item(item_id: str, query: str,
                       builtin: str | None = None) -> dict[str, Any]:
    """A1 carve-out: a hosted builtin call is a self-contained item.

    The Responses protocol has no separate result item for hosted tools, so
    replay needs no pairing. Takes the query already extracted — the sync path
    reads a parsed args dict while the stream path parses a raw JSON string,
    and the two are not interchangeable (see _builtin_query).

    A tool search is a *different item type* from a web search. The OpenAI SDK
    models them separately — ``ResponseToolSearchCall`` declares
    ``type: "tool_search_call"`` with ``arguments``/``execution``, whereas
    ``ResponseFunctionWebSearch`` declares ``type: "web_search_call"`` with
    ``action.query``. Labelling a tool-search step as a web search told the
    client a search that never happened had run, and hid the real one.
    """
    if _builtin_is_tool_search(builtin):
        return {"type": "tool_search_call", "id": item_id, "status": "completed",
                "execution": "server", "call_id": None,
                "arguments": {"query": query}}
    return {"type": "web_search_call", "id": item_id, "status": "completed",
            "action": {"type": "search", "query": query}}


def _builtin_query(arguments: str) -> str:
    """Extract the search query from a hosted call's raw argument string."""
    try:
        args = orjson.loads(arguments) if arguments else {}
    except orjson.JSONDecodeError:
        args = {}
    # A well-formed JSON *scalar* or array parses cleanly but has no .get: an
    # upstream that streamed `[1]`, `5` or `"abc"` as the arguments crashed
    # the encoder mid-stream, after content had already been sent (AUDIT
    # #165). The sync path above reads a parsed dict and needs no guard; only
    # a JSON object can carry a query.
    if not isinstance(args, dict):
        return ""
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
        if t.builtin is not None:
            # Provider-hosted call (web_search, ...): render as a hosted item
            # rather than a phantom function call the client cannot dispatch.
            # Keyed on the flag, not the name (AUDIT #156).
            # Read the parsed dict, not raw_args: providers set both, but a
            # raw_args that failed to parse must not blank an available query.
            output.append(_builtin_call_item(t.id, t.args.get("query", ""),
                                             t.builtin))
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
                                      _builtin_query(t["args"]),
                                      t.get("builtin"))
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
            # A text delta must never close an open tool item: _close_item
            # POPS the tool, so its later args fragments would be dropped and
            # output_item.done would fire mid-stream (Codex CLI counts a
            # half-finished call). Interleaved text is suppressed — the same
            # policy as the Anthropic encoder — and the tool's args keep
            # streaming legally on their own output_index.
            if self._item_open == "tool":
                return None
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
            # Same interleave policy as TextDelta: never close an open tool
            # item from a thinking delta (round-25 proved this class of bug
            # for text; thinking has the identical _close_item hazard).
            if self._item_open == "tool":
                return None
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
            # A1 carve-out: hosted builtin calls open as self-contained items
            # (replay-safe: no separate result item exists in this protocol).
            # A tool search opens as its own item type, not a web search.
            if d.builtin is not None:
                if _builtin_is_tool_search(d.builtin):
                    item: dict[str, Any] = {
                        "type": "tool_search_call",
                        "id": d.id or f"ts_{self.req_id}_{n}",
                        "status": "in_progress", "execution": "server",
                        "call_id": None, "arguments": {}}
                else:
                    item = {"type": "web_search_call",
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
            # Accumulate even for builtins — the close-time _builtin_query read
            # needs the buffer — but emit no frame: the item opened as a
            # self-contained web_search_call, so a function_call_arguments
            # frame would reference a phantom fc_<req>_<n> id that never had
            # an output_item.added (Codex CLI accumulates fragments against a
            # nonexistent function item).
            t["args"] += d.args_fragment
            if t.get("builtin"):
                return None
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
            # Close every open output item before the failure, exactly as the
            # success path does in _completed() (AUDIT #111). Without this a
            # client that already saw response.output_item.added never gets the
            # matching output_item.done and the item stays in_progress forever;
            # the Anthropic encoder closes its blocks on the same path
            # (AUDIT #212's sibling asymmetry).
            closing = b"".join(self._close_item())
            for idx in sorted(self._tools):
                closing += b"".join(self._close_tool(idx))
            return closing + self._evt("response.failed", {
                "response": {"id": f"resp_{self.req_id}", "status": "failed",
                             "error": {"code": "api_error", "message": d.message}}})
        return None

    def _completed(self) -> bytes:
        # A legal stream always closes the currently open output item before
        # the terminal event, even when the last delta left one open.
        closing = b"".join(self._close_item())
        # Parallel tool calls are siblings, not sequential: an adapter may end
        # the stream with several function_call items still open.
        # _close_item()/_close_tool() only close the *current* one, and
        # _close_tool POPS its entry from self._tools — so every remaining
        # index needs an explicit close or the client never sees that call's
        # output_item.done and it is missing from the terminal payload
        # (AUDIT #111).
        for idx in sorted(self._tools):
            closing += b"".join(self._close_tool(idx))
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
