"""Anthropic Messages wire codec: /v1/messages decode + SSE encode (fsm_anthropic)."""

from __future__ import annotations

import json
from typing import Any

import orjson

from wiwi.core.context import RequestContext
from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import sse_frame
from wiwi.wire.openai_chat import DialectError

# Cap on text/thinking buffered while a tool_use block is open (see
# AnthropicStreamEncoder._deferred). Anthropic content blocks are strictly
# sequential, so interleaved text cannot be emitted inline and must be held —
# but a stream that interleaves unboundedly behind one long-open tool block
# would grow the buffer without limit. Same unbounded-growth class that
# MAX_TOOL_ARGS_BYTES (streaming/validation.py) and the coalescer's max_bytes
# already guard. On overflow the OLDEST entries are dropped, keeping the most
# recent content — what the model is saying now is what the client still needs.
MAX_DEFERRED_CHARS = 256 * 1024


def decode_request(body: dict[str, Any]) -> ir.Request:
    if not isinstance(body.get("model"), str) or not body["model"]:
        raise DialectError("'model' is required")
    messages: list[ir.Message] = []
    system_text = body.get("system")
    if isinstance(system_text, str) and system_text:
        messages.append(ir.Message(role="system", parts=[ir.TextPart(system_text)]))
    elif isinstance(system_text, list):
        parts = [ir.TextPart(b.get("text", "") if isinstance(b.get("text"), str) else "",
                             cache_control=b.get("cache_control"))
                 for b in system_text
                 if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            messages.append(ir.Message(role="system", parts=parts))
    raw_messages = body.get("messages")
    if raw_messages is not None and not isinstance(raw_messages, list):
        # Mirror of the chat codec's guard: a non-list `messages` is not
        # iterable the way this loop needs, and the TypeError escaped as a 500.
        raise DialectError("'messages' must be a list")
    for m in raw_messages or []:
        if not isinstance(m, dict):
            continue  # malformed entry: skip rather than 500 on .get
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
                if not isinstance(btype, str):
                    # Missing (or non-string) type: no branch below matches it,
                    # and the `*_tool_result` arm calls .endswith on it — which
                    # raised AttributeError and turned one junk block into a
                    # gateway 500, since run_chat_like only catches
                    # DialectError/ValueError. Skip like the dict guard above.
                    continue
                if btype == "text":
                    raw_text = b.get("text", "")
                    parts.append(ir.TextPart(raw_text if isinstance(raw_text, str) else "",
                                             cache_control=b.get("cache_control")))
                elif btype == "image":
                    src = b.get("source") or {}
                    if not isinstance(src, dict):
                        # `or {}` does not help a truthy string/number: the
                        # block has no source to read, so skip it like the
                        # non-string btype guard above rather than 500.
                        continue
                    if src.get("type") == "base64":
                        raw_b64 = src.get("data")
                        if not isinstance(raw_b64, str) or not raw_b64:
                            # A base64 source with no payload is junk, not an
                            # image: it used to be re-emitted upstream as
                            # ``"data": null`` — a 400 that names no offending
                            # block (AUDIT #168). Drop it, like the other
                            # malformed blocks in this loop.
                            continue
                        raw_mime = src.get("media_type")
                        parts.append(ir.ImagePart(
                            b64=raw_b64,
                            # An explicit ``"media_type": null`` passed the
                            # dict.get default and reached the upstream as
                            # null (AUDIT #187).
                            mime=(raw_mime if isinstance(raw_mime, str)
                                  else "image/png"),
                            cache_control=b.get("cache_control")))
                    elif src.get("type") == "url":
                        raw_url = src.get("url")
                        if not isinstance(raw_url, str) or not raw_url:
                            continue  # no URL to fetch: same junk as above
                        parts.append(ir.ImagePart(
                            url=raw_url,
                            cache_control=b.get("cache_control")))
                    elif src.get("type") == "file":
                        raw_fid = src.get("file_id")
                        if not isinstance(raw_fid, str) or not raw_fid:
                            # With no file_id the part has no source at all and
                            # the adapters re-render it as a base64 image with
                            # ``data: null`` — the same bogus media block
                            # (AUDIT #168).
                            continue
                        parts.append(ir.ImagePart(
                            file_id=raw_fid,
                            cache_control=b.get("cache_control")))
                elif btype == "document":
                    src = b.get("source") or {}
                    if not isinstance(src, dict):
                        continue  # same non-dict source guard as the image arm
                    if src.get("type") == "base64":
                        raw_b64 = src.get("data")
                        if not isinstance(raw_b64, str) or not raw_b64:
                            continue  # no payload: same junk as the image arm
                        raw_mime = src.get("media_type")
                        parts.append(ir.DocumentPart(
                            b64=raw_b64,
                            mime=(raw_mime if isinstance(raw_mime, str)
                                  else "application/pdf"),
                            name=b.get("title"), context=b.get("context"),
                            cache_control=b.get("cache_control")))
                    elif src.get("type") == "url":
                        raw_url = src.get("url")
                        if not isinstance(raw_url, str) or not raw_url:
                            continue
                        parts.append(ir.DocumentPart(
                            url=raw_url, name=b.get("title"),
                            context=b.get("context"),
                            cache_control=b.get("cache_control")))
                elif btype == "tool_use":
                    raw_name = b.get("name")
                    parts.append(ir.ToolUsePart(
                        id=b.get("id", ""),
                        # ``b.get("name", "")`` defaults only a MISSING key:
                        # an explicit JSON null passed straight through and was
                        # re-emitted upstream as ``"name": null`` (AUDIT #186).
                        # Coerce like the text fields above.
                        name=raw_name if isinstance(raw_name, str) else "",
                        args=b.get("input") or {}))
                elif btype == "server_tool_use" or btype == "mcp_tool_use":
                    # Server-side tools (web_search, code_execution, mcp, ...):
                    # the PROVIDER executes these, so tag them builtin — the
                    # client never dispatched them and cannot return a result.
                    # Keeping the original block type matters on replay: a
                    # ``mcp_tool_use`` paired with an ``mcp_tool_result`` must
                    # go back as ``mcp_tool_use`` or the result is unpaired and
                    # Anthropic rejects the history (AUDIT #156).
                    raw_name = b.get("name")
                    name = raw_name if isinstance(raw_name, str) else ""
                    parts.append(ir.ToolUsePart(
                        id=b.get("id", ""), name=name,
                        args=b.get("input") or {},
                        builtin=name or "server_tool",
                        block_type=btype))
                elif btype in ("search_result", "container_upload",
                               "tool_reference"):
                    # Blocks the IR has no first-class part for. They carry
                    # content the model needs (a search result body, an
                    # uploaded file reference, a deferred MCP tool reference),
                    # so render them as text rather than dropping the block —
                    # silently losing a ``tool_reference`` meant the model
                    # never learned which deferred tool to load (AUDIT #156).
                    rendered = json.dumps(
                        {k: v for k, v in b.items() if k != "type"})
                    parts.append(ir.TextPart(rendered))
                elif btype == "tool_result" or btype.endswith("_tool_result"):
                    # Covers user tool_result AND the server-tool result
                    # family (web_search_tool_result, code_execution_tool_result,
                    # mcp_tool_result, computer_tool_result, browser_tool_result).
                    c = b.get("content")
                    images: list[ir.ImagePart] = []
                    extra: list[dict[str, Any]] = []
                    if isinstance(c, str):
                        text = c
                    elif isinstance(c, list):
                        texts = [blk["text"] for blk in c
                                 if isinstance(blk, dict) and blk.get("type") == "text"
                                 and isinstance(blk.get("text"), str)]
                        joined = " ".join(t for t in texts if t)
                        if joined:
                            text = joined
                        else:
                            # keep non-text blocks, never base64 image blobs
                            others = [b for b in c if isinstance(b, dict)
                                      and b.get("type") not in ("image", "input_image")]
                            text = json.dumps(others) if others else ""
                        for blk in c:
                            if not isinstance(blk, dict):
                                continue
                            # Multimodal tool results: collect image blocks
                            # (base64/url/file sources) so providers with
                            # native image support can re-emit them.
                            if blk.get("type") == "image":
                                src = blk.get("source") or {}
                                if not isinstance(src, dict):
                                    continue  # non-dict source: skip, don't 500
                                if src.get("type") == "base64":
                                    raw_b64 = src.get("data")
                                    if not isinstance(raw_b64, str) or not raw_b64:
                                        # No payload: the part re-encodes as
                                        # ``"data": null`` (AUDIT #168). Drop it.
                                        continue
                                    raw_mime = src.get("media_type")
                                    images.append(ir.ImagePart(
                                        b64=raw_b64,
                                        mime=(raw_mime if isinstance(raw_mime, str)
                                              else "image/png"),
                                        cache_control=blk.get("cache_control")))
                                elif src.get("type") == "url":
                                    raw_url = src.get("url")
                                    if not isinstance(raw_url, str) or not raw_url:
                                        continue  # no URL: same junk as above
                                    images.append(ir.ImagePart(
                                        url=raw_url,
                                        cache_control=blk.get("cache_control")))
                                elif src.get("type") == "file":
                                    raw_fid = src.get("file_id")
                                    if not isinstance(raw_fid, str) or not raw_fid:
                                        continue  # no file_id: no source at all
                                    images.append(ir.ImagePart(
                                        file_id=raw_fid,
                                        cache_control=blk.get("cache_control")))
                                continue
                            # Blocks with no IR representation but real meaning
                            # for the model — above all ``tool_reference``,
                            # which is how tool search tells the model WHICH
                            # deferred tool to load. Keeping them only when the
                            # result carried no text at all meant the common
                            # "Found 1 tool. + tool_reference" result lost the
                            # reference and the model never discovered the
                            # tool (it saw a sentence and nothing else).
                            # Carried verbatim for the Anthropic encoder;
                            # dialects without the block still get ``content``.
                            if blk.get("type") != "text":
                                extra.append(dict(blk))
                    elif c is None:
                        text = ""
                    else:
                        text = json.dumps(c)
                    parts.append(ir.ToolResultPart(tool_use_id=b.get("tool_use_id", ""),
                                                   content=text,
                                                   is_error=bool(b.get("is_error")),
                                                   cache_control=b.get("cache_control"),
                                                   images=images,
                                                   extra_blocks=extra,
                                                   block_type=btype))
                elif btype == "thinking":
                    raw_think = b.get("thinking", "")
                    sig = b.get("signature")
                    # Anthropic streams a null thinking value on redacted-
                    # thinking turns; ThinkingPart.text is typed str and every
                    # downstream consumer concatenates it — coerce once here
                    # rather than let None crash the adapters (TypeError) or
                    # leak "thinking": null back upstream.
                    parts.append(ir.ThinkingPart(
                        raw_think if isinstance(raw_think, str) else "",
                        sig if isinstance(sig, str) else None))
                elif btype == "redacted_thinking":
                    # Encrypted thinking block: preserve the blob verbatim so
                    # an Anthropic upstream receives it back on replay.
                    # Dropping it breaks tool-use continuity (the API
                    # requires the prior assistant turn to carry it); other
                    # adapters see empty text and skip it.
                    raw_data = b.get("data", "")
                    parts.append(ir.ThinkingPart(
                        text="", block_type="redacted_thinking",
                        data=raw_data if isinstance(raw_data, str) else ""))
        if parts:
            # ``role`` is one of user/assistant/system. A ``system`` entry
            # appended MID-conversation (the mid-conversation-system beta) is a
            # real role the API accepts; rewriting it to ``user`` both weakens
            # the instruction and moves it to a different cache-prefix
            # position, which drops its cache_control breakpoint. Only
            # ``tool`` has no Messages representation, so it alone folds into
            # user (AUDIT #156).
            normalized = role if role in ("user", "assistant", "system") else "user"
            messages.append(ir.Message(role=normalized, parts=parts))
        elif role == "assistant":
            # ``content: null`` is legal Anthropic input — it is what the API
            # itself emits for a tool-use-only turn, and Claude Code replays it
            # verbatim. Dropping the turn (there was no ``elif`` arm) collapsed
            # two consecutive user turns into one, corrupting turn alternation
            # on replayed history (AUDIT #167). Append an empty-parts assistant
            # message, exactly as the Chat codec does.
            messages.append(ir.Message(role="assistant", parts=[]))

    tools: list[ir.Tool] = []
    raw_tools = body.get("tools")
    if raw_tools is not None and not isinstance(raw_tools, list):
        raise DialectError("'tools' must be a list")  # non-iterable -> 500
    for t in raw_tools or []:
        if not isinstance(t, dict):
            continue  # junk entry: skip rather than crash the whole request
        ttype = t.get("type")
        if not isinstance(ttype, str) and ttype is not None:
            # A non-string type (a list, a dict) is unhashable: `ttype in (...)`
            # and bt.canonical_for's dict lookups raise TypeError -> 500. Only
            # None means "function tool with the type omitted"; anything else
            # non-string is junk, so skip it like the dict guard above.
            continue
        if ttype in (None, "custom", "function"):
            # Plain function tool (Anthropic function tools may omit "type").
            raw_schema = t.get("input_schema")
            raw_name = t.get("name")
            raw_desc = t.get("description")
            tools.append(ir.Tool(
                # An explicit JSON null is not a missing key: dict.get returned
                # the null and it reached the upstream verbatim, so the model
                # saw a nameless tool (AUDIT #186). Coerce like the text fields.
                name=raw_name if isinstance(raw_name, str) else "",
                description=raw_desc if isinstance(raw_desc, str) else "",
                # A non-dict input_schema is otherwise stored verbatim and
                # crashes validate_tool_args inside the stream pump, cooling a
                # healthy deployment for a caller-controlled shape. Coerce to
                # the same empty-object default the missing-schema case gets.
                parameters_json_schema=(raw_schema if isinstance(raw_schema, dict)
                                        else {"type": "object"}),
                strict=t.get("strict"),
                input_examples=t.get("input_examples"),
                defer_loading=t.get("defer_loading"),
                cache_control=t.get("cache_control")))
            continue
        canonical = bt.canonical_for("anthropic", ttype)
        if canonical is not None:
            # Provider-hosted builtin (web_search family): keep the canonical
            # name + config subset; never emit it as a function tool.
            config = {k: t[k] for k in bt.BUILTIN_CONFIG_KEYS if k in t}
            tools.append(ir.Tool(name=t.get("name") or canonical,
                                 builtin=canonical,
                                 builtin_config=config,
                                 cache_control=t.get("cache_control")))
        else:
            # Unknown server tool (code_execution, computer, ...): preserve it
            # as an unmapped builtin so no surface mangles it into a function
            # tool; providers that can't host it drop it with a warning.
            tools.append(ir.Tool(
                name=t.get("name") or ttype,
                builtin=ttype,
                builtin_config={bt.WIRE_TYPE_KEY: ttype},
                cache_control=t.get("cache_control")))
    tc_raw = body.get("tool_choice") or {}
    tool_choice: ir.ToolChoice | None = None
    disable_parallel: bool | None = None
    if isinstance(tc_raw, dict):
        disable_parallel = tc_raw.get("disable_parallel_tool_use")
        tc_type = tc_raw.get("type")
        if tc_type == "any":
            tool_choice = ir.ToolChoiceRequired()
        elif tc_type == "tool":
            raw_tc_name = tc_raw.get("name")
            # Same explicit-null trap as the tool blocks: ``.get(k, "")``
            # defaults only a missing key, so ``"name": null`` reached the
            # upstream as null (AUDIT #186).
            tool_choice = ir.ToolChoiceNamed(
                raw_tc_name if isinstance(raw_tc_name, str) else "")
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
    oc = body.get("output_config") if isinstance(body.get("output_config"), dict) else {}
    oc_fmt = oc.get("format")
    if isinstance(oc_fmt, dict) and oc_fmt.get("type") == "json_schema":
        response_format = ir.ResponseFormat(
            type="json_schema", json_schema=oc_fmt.get("schema"),
            name=oc_fmt.get("name"), strict=oc_fmt.get("strict"))
    # output_config.effort is where Claude Code's /effort command, the effort
    # slider, --effort and CLAUDE_CODE_EFFORT_LEVEL all land. Only ``format``
    # used to be read and ``output_config`` was not in _PASSTHROUGH_KEYS, so
    # every effort selection was silently discarded and the model always ran at
    # the API default (AUDIT #156).
    effort = oc.get("effort")
    effort = effort if isinstance(effort, str) and effort else None
    budget_raw = thinking.get("budget_tokens") if thinking_type == "enabled" else None
    # Coerce numeric strings ("1024") and reject garbage: a str budget reaches
    # the Anthropic adapter's `<=`/`>` comparisons and raises TypeError — a
    # gateway 500 on a merely-odd client value.
    thinking_budget: int | None = None
    if isinstance(budget_raw, bool):
        thinking_budget = None
    elif isinstance(budget_raw, int):
        thinking_budget = budget_raw
    # Numeric strings ("1024") and whole floats (2048.0) are legal JSON
    # clients send; coerce. Anything else is unusable — None, so the
    # adapters never see a str/float where they expect int (TypeError 500).
    elif ((isinstance(budget_raw, str) and budget_raw.strip().isdigit())
            or (isinstance(budget_raw, float) and budget_raw.is_integer())):
        thinking_budget = int(budget_raw)
    else:
        thinking_budget = None
    mt_raw = body.get("max_tokens")
    if isinstance(mt_raw, bool):
        max_tokens = None
    elif isinstance(mt_raw, int):
        max_tokens = mt_raw
    elif ((isinstance(mt_raw, str) and mt_raw.strip().isdigit())
            or (isinstance(mt_raw, float) and mt_raw.is_integer())):
        max_tokens = int(mt_raw)
    else:
        max_tokens = None
    # stop_sequences: the spec says list[str], but a bare string is a common
    # client mistake — treat it as ONE sequence, not one per character.
    stop_raw = body.get("stop_sequences")
    if isinstance(stop_raw, str):
        stop_seqs: list[str] = [stop_raw]
    elif isinstance(stop_raw, list):
        stop_seqs = [s for s in stop_raw if isinstance(s, str)]
    else:
        stop_seqs = []
    g = ir.GenParams(
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        max_tokens=max_tokens,
        stop=stop_seqs,
        thinking_budget=thinking_budget,
        thinking_type=thinking_type,
        reasoning_effort=reasoning_effort,
        effort=effort,
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

# IR StopReason -> Anthropic stop_reason. The IR now carries Anthropic's own
# vocabulary (pause_turn, stop_sequence, context_window_exceeded, compaction),
# so those round-trip unchanged; the remaining IR values come from the narrower
# dialects and map onto their closest Anthropic spelling.
_STOP_REASON_OUT: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_call": "tool_use",
    "content_filter": "refusal",
    "pause_turn": "pause_turn",
    "stop_sequence": "stop_sequence",
    "context_window_exceeded": "model_context_window_exceeded",
    "compaction": "compaction",
}


def encode_response(ctx: RequestContext, turn: ir.AssistantTurn, model: str,
                    req_id: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    # Provider-executed result blocks, grouped by the call they answer so each
    # one lands directly after its ``server_tool_use`` (the order the API
    # itself emits). Blocks whose id matches no call stay in the leftover pass
    # below rather than being dropped.
    _blocks_by_call: dict[str, list[dict[str, Any]]] = {}
    for blk in turn.server_blocks:
        tid = blk.get("tool_use_id")
        if isinstance(tid, str) and tid:
            _blocks_by_call.setdefault(tid, []).append(blk)
    _result_ids = set(_blocks_by_call)
    for t in turn.thinking:
        if t.block_type == "redacted_thinking":
            # Encrypted thinking must be re-emitted verbatim: the client
            # replays this content on the next turn, and a blob rendered as an
            # empty, unsigned ``thinking`` block is 400-bait upstream. The
            # streaming encoder has had this branch since #103; the sync path
            # was the missing mirror (AUDIT #125).
            content.append({"type": "redacted_thinking", "data": t.data or ""})
            continue
        tb: dict[str, Any] = {"type": "thinking", "thinking": t.text}
        if t.signature:
            tb["signature"] = t.signature
        content.append(tb)
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for t in turn.tool_calls:
        # A1: a provider-hosted call with NO result block to pair it with is
        # suppressed — the client would try to execute a phantom function, and
        # an unpaired server_tool_use in replayed history is rejected by
        # Anthropic. A call whose result IS present is emitted as
        # ``server_tool_use`` and followed by that result, which is the shape
        # the API itself produces. Keyed on the ``builtin`` flag, NOT the tool
        # name: a caller may legitimately define a function tool called
        # ``web_search``, and a name match deleted that real call from the
        # response entirely (AUDIT #156).
        if t.builtin is not None:
            if t.id and t.id in _result_ids:
                content.append({"type": t.block_type or "server_tool_use",
                                "id": t.id, "name": t.name, "input": t.args})
                content.extend(_blocks_by_call.pop(t.id, []))
            continue
        content.append({"type": "tool_use", "id": t.id, "name": t.name,
                        "input": t.args})
    # Results whose call the upstream never streamed (or that arrived after the
    # call list was built) still belong in the turn: dropping them would lose
    # the search hits the model needs to answer.
    for leftover in _blocks_by_call.values():
        content.extend(leftover)
    if not content:
        content = [{"type": "text", "text": ""}]
    u = turn.usage
    sr = _STOP_REASON_OUT.get(turn.stop_reason, "end_turn")
    # A1 downgrade guard: tool_call with every call suppressed is invalid.
    # A paired ``server_tool_use`` is not a tool_use block, so it does not
    # satisfy this check either — but such a turn also ends with end_turn
    # upstream (the provider ran the tool itself), so the guard stands.
    if sr == "tool_use" and not any(b["type"] == "tool_use" for b in content):
        sr = "end_turn"
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
        # A1 stop_reason guard: any real (non-builtin) tool_use block emitted?
        # A suppressed-builtin-only stream must not finish with stop_reason
        # tool_use — Anthropic rejects a tool_use finish with no tool_use block.
        self._saw_tool_use = False
        # Provider-executed calls awaiting their result block, keyed by IR
        # index. Held rather than emitted so a call whose result never arrives
        # is dropped (unpaired ``server_tool_use`` is rejected on replay) while
        # a complete search turn is passed through with its results.
        self._server_calls: dict[int, dict[str, Any]] = {}
        # Signature seen while no thinking block is open (cross-provider quirk);
        # flushed into the next thinking block right before it closes.
        self._pending_sig: str | None = None
        # Block index of the most recent thinking block, so a late pending
        # signature stamps the RIGHT block — never a later thinking block.
        self._last_think_idx: int | None = None
        self._usage: dl.UsageFinal | None = None
        self._stop = "end_turn"
        self._stop_seq: str | None = None
        # Text/thinking that arrived while a tool_use block was open. Anthropic
        # content blocks are strictly sequential, so such content cannot be
        # emitted inline — but it must not be discarded either: it is part of
        # the answer and the client replays it on the next turn. Buffer it as
        # ("text"|"thinking", text, signature) and emit it as its own block as
        # soon as the tool block closes (AUDIT #156).
        self._deferred: list[tuple[str, str, str | None]] = []
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

    def _flush_deferred(self) -> list[bytes]:
        """Emit buffered interleaved text/thinking as their own blocks.

        Only safe to call when no tool block is open: the buffer exists
        precisely because a tool block was in the way. Runs of the same kind
        coalesce into one block, so text-then-thinking yields two blocks and
        thinking-then-text yields two, matching how the real API interleaves
        content around a tool call.
        """
        if not self._deferred:
            return []
        out: list[bytes] = []
        for kind, text, sig in self._deferred:
            if self._open_block != kind:
                out.extend(self._close_block())
                out.append(self._evt("content_block_start", {
                    "type": "content_block_start", "index": self._block_idx,
                    "content_block": {"type": kind,
                                      kind if kind == "thinking" else "text": ""}}))
                self._open_block = kind
                self._block_idx += 1
            if kind == "thinking":
                thd = self._think_delta
                thd["index"] = self._block_idx - 1
                thd["delta"]["thinking"] = text
                out.append(self._evt("content_block_delta", thd))
                if sig:
                    self._pending_sig = sig
            else:
                td = self._text_delta
                td["index"] = self._block_idx - 1
                td["delta"]["text"] = text
                out.append(self._evt("content_block_delta", td))
        self._deferred.clear()
        return out

    def _take_server_call(self, block: dict[str, Any]) -> dict[str, Any] | None:
        """Pop the buffered provider call that *block* answers, if any.

        Matching is by ``tool_use_id`` first (authoritative), falling back to
        the single remaining buffered call when the result names an id the
        adapter never saw — an upstream that omitted the call's id still gets
        its result emitted rather than dropped.
        """
        tid = block.get("tool_use_id")
        for index, call in self._server_calls.items():
            if tid and call["id"] == tid:
                return self._server_calls.pop(index)
        if len(self._server_calls) == 1 and not tid:
            return self._server_calls.pop(next(iter(self._server_calls)))
        return None

    def _defer(self, kind: str, text: str, sig: str | None) -> None:
        """Buffer interleaved content, evicting the oldest past the cap.

        See ``MAX_DEFERRED_CHARS``. Evicts from the front so the newest content
        survives, and trims the boundary entry from its head rather than
        dropping it whole — so the buffer always ends up within the cap, even
        when a single delta is itself larger than the cap.
        """
        self._deferred.append((kind, text, sig))
        total = sum(len(t) for _, t, _ in self._deferred)
        while total > MAX_DEFERRED_CHARS and self._deferred:
            k, t, s = self._deferred.pop(0)
            total -= len(t)
            if total < MAX_DEFERRED_CHARS:
                # This pop overshot the cap: put back the entry's TAIL (the
                # newest part of it) so the buffer lands exactly at the cap
                # instead of losing content it had room for.
                keep = MAX_DEFERRED_CHARS - total
                if keep > 0 and t:
                    self._deferred.insert(0, (k, t[-keep:], s))
                    total += keep
                break
        # A fully-evicted entry leaves nothing to emit; drop empties so the
        # flush loop never opens a block with no content.
        self._deferred = [e for e in self._deferred if e[1]]

    def _drain_deferred(self) -> list[bytes]:
        """Flush buffered interleaved content AND close the block it opened.

        ``_flush_deferred`` deliberately leaves its last block open (a later
        text/thinking delta continues in it, and ``final_frame`` stops it).
        A caller about to emit a WHOLE block of its own must not inherit that
        state: the block would never be stopped, and the new block's start
        would reuse the open block's index — one index with two stops and one
        with none (AUDIT #178).
        """
        if not self._deferred:
            return []
        return self._flush_deferred() + self._close_block()

    def _emit_server_call(self, call: dict[str, Any]) -> list[bytes]:
        """Emit a buffered provider call as a complete content block.

        Delivered whole (the adapter buffers its args), so it opens, carries
        its input, and closes in one frame. Emitted as ``server_tool_use`` (or
        the original ``mcp_tool_use`` spelling) rather than ``tool_use``: the
        client must not try to execute it, and the API requires the result that
        follows to be paired with the same type.
        """
        out: list[bytes] = []
        if self._open_block is not None:
            out.extend(self._close_block())
        # Deferred content drains BEFORE the call's own block opens: draining
        # opens a text/thinking block and bumps ``_block_idx``, so leaving it
        # open made the start/stop below reuse that block's index and left it
        # unstopped (AUDIT #178).
        out.extend(self._drain_deferred())
        raw = "".join(call["args"])
        out.append(self._evt("content_block_start", {
            "type": "content_block_start", "index": self._block_idx,
            "content_block": {"type": call["block_type"], "id": call["id"],
                              "name": call["name"], "input": {}}}))
        if raw:
            jd = self._json_delta
            jd["index"] = self._block_idx
            jd["delta"]["partial_json"] = raw
            out.append(self._evt("content_block_delta", jd))
        out.append(self._evt("content_block_stop",
                             {"type": "content_block_stop",
                              "index": self._block_idx}))
        self._block_idx += 1
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
            # Prompt/cache usage when the provider reports it up front
            # (Anthropic does). Claude Code reads this frame to size the
            # context window and decide when to auto-compact, so emitting
            # zeros left its meter pinned at 0% for the entire session
            # (AUDIT #156). Providers that report usage only at the end leave
            # these zero and the trailing message_delta carries the totals.
            return self._evt("message_start", {
                "type": "message_start",
                "message": {"id": f"msg_{self.req_id}", "type": "message",
                            "role": "assistant", "model": self.model, "content": [],
                            "stop_reason": None, "stop_sequence": None,
                            "usage": {"input_tokens": d.prompt,
                                      "output_tokens": 0,
                                      "cache_read_input_tokens": d.cached,
                                      "cache_creation_input_tokens":
                                          d.cache_creation}}})
        if isinstance(d, dl.TextDelta):
            # A text delta can never be emitted while a tool_use block is
            # open: Anthropic content blocks are strictly sequential, and
            # closing the tool block would lose its index mapping so a later
            # ToolCallArgsDelta would land on the text block (Claude Code
            # rejects "Content block is not a input_json block"). Defer it
            # instead of dropping it — the text is part of the answer and the
            # client replays it on the next turn, so discarding it made the
            # model's own prose invisible to both the user and itself
            # (AUDIT #156).
            if self._open_block == "tool":
                self._defer("text", d.text, None)
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
            if d.block_type == "redacted_thinking":
                # Anthropic redacted thinking: emit the opaque block verbatim so
                # the client can replay it on the next turn. The blob must not
                # be merged into a thinking/text block (AUDIT #103).
                out = []
                if self._open_block is not None:
                    out.extend(self._close_block())
                out.append(self._evt("content_block_start", {
                    "type": "content_block_start", "index": self._block_idx,
                    "content_block": {"type": "redacted_thinking",
                                      "data": d.data or ""}}))
                self._block_idx += 1
                out.append(self._evt("content_block_stop",
                                     {"type": "content_block_stop",
                                      "index": self._block_idx - 1}))
                return b"".join(out)
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
            # Thinking with text while a tool block is open: defer it, like
            # interleaved text. It is part of the model's reasoning, and the
            # client replays thinking blocks on the next turn (AUDIT #156).
            if self._open_block == "tool":
                self._defer("thinking", d.text, d.signature)
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
            # A provider-hosted call (web_search, tool_search, an MCP tool) is
            # BUFFERED, not suppressed: it is emitted only if its result block
            # arrives, because the API pairs them and an unpaired
            # ``server_tool_use`` in replayed history is rejected. A stream
            # that never delivers the result leaves the buffer to be discarded
            # at final_frame, which preserves the old A1 behaviour for the
            # half-trace case (AUDIT #156) while no longer throwing away a
            # complete search turn (AUDIT #158).
            if d.builtin is not None:
                self._server_calls[d.index] = {
                    "id": d.id if isinstance(d.id, str) else str(d.id),
                    "name": d.name,
                    "block_type": d.block_type or "server_tool_use",
                    "args": [],
                }
                return None
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
            self._saw_tool_use = True
            self._tool_blocks[d.index] = self._block_idx
            self._open_tool = d.index
            self._open_block = "tool"
            self._block_idx += 1
            return b"".join(out)
        if isinstance(d, dl.ToolCallArgsDelta):
            pending = self._server_calls.get(d.index)
            if pending is not None:
                pending["args"].append(d.args_fragment)
                return None
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
            if d.index in self._server_calls:
                # Keep the buffered call: its result decides whether it is
                # emitted at all.
                return None
            out = self._close_block(tool_index=d.index)
            # Interleaved text/thinking buffered while this tool was open can
            # now be emitted — but only once NO tool block is open, so parallel
            # siblings keep their consecutive indices and a text block cannot
            # land in the middle of the tool group (AUDIT #156).
            if self._open_block != "tool" and self._deferred:
                out.extend(self._flush_deferred())
            return b"".join(out)
        if isinstance(d, dl.ServerToolResultDelta):
            # A provider-executed tool's result. Flush its buffered call first
            # so the pair is emitted in the order the API itself produces, then
            # the result as its own block. Both are delivered whole, so each
            # opens and closes in one frame — but any text/thinking block must
            # close first, and buffered interleaved content must drain, because
            # content blocks are strictly sequential.
            out = []
            call = self._take_server_call(d.block)
            if call is not None:
                out.extend(self._emit_server_call(call))
            if self._open_block is not None:
                out.extend(self._close_block())
            # Same hazard as ``_emit_server_call`` (AUDIT #178): flushing
            # leaves the text/thinking block it opened still open, so the
            # result block below would reuse its index and leave it unstopped.
            out.extend(self._drain_deferred())
            out.append(self._evt("content_block_start", {
                "type": "content_block_start", "index": self._block_idx,
                "content_block": d.block}))
            out.append(self._evt("content_block_stop",
                                 {"type": "content_block_stop",
                                  "index": self._block_idx}))
            self._block_idx += 1
            return b"".join(out)
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None
        if isinstance(d, dl.Finish):
            self._stop = _STOP_REASON_OUT.get(d.stop_reason, "end_turn")
            self._stop_seq = d.stop_sequence
            # A1 downgrade guard: suppression may have removed the only tool
            # call — a tool_use stop_reason with no tool_use block is invalid.
            if self._stop == "tool_use" and not self._saw_tool_use:
                self._stop = "end_turn"
                # The matched sequence is only meaningful alongside
                # stop_reason "stop_sequence"; leaving it set on a downgraded
                # turn makes a client that branches on stop_sequence != null
                # see a spurious match (AUDIT #156).
                self._stop_seq = None
            return None
        if isinstance(d, dl.StreamEnd):
            return None  # caller emits message_delta (final_frame) then message_stop
        if isinstance(d, dl.StreamError):
            # Close whatever is open BEFORE the error frame. A stream that
            # dies mid-text or mid-tool-args would otherwise leave a
            # content_block_start with no matching stop, so a client that
            # accumulates the SSE sees a tool_use block whose ``input`` never
            # finished parsing — the tool renders with empty arguments
            # (AUDIT #156). StreamError may terminate at any point, so this is
            # the last chance to make the emitted stream well-formed.
            out = self._close_block()
            for idx in sorted(self._tool_blocks):
                out.extend(self._close_block(tool_index=idx))
            out.append(self._evt("error", {
                "type": "error",
                "error": {"type": _stream_error_type(d),
                          "message": d.message}}))
            return b"".join(out)
        return None

    def final_frame(self) -> bytes:
        # Content buffered while a tool block was open is emitted first, as its
        # own block, so it lands inside the message rather than being lost
        # (AUDIT #156). Safe here: the tool blocks below are still open, but
        # _flush_deferred closes whatever it needs and the tool indices are
        # tracked separately from _open_block.
        out = b"".join(self._flush_deferred())
        # A legal stream always closes the currently open content block before
        # the terminating message_delta, even when the last delta left one open.
        out += b"".join(self._close_block())
        # Parallel tool calls are siblings: an adapter may end the message with
        # several tool_use blocks still open (the Anthropic upstream omits their
        # content_block_stop). _close_block() only closes the *current* one, so
        # every remaining registered index needs its own stop or the client is
        # left with a tool_use block that never finishes (AUDIT #111).
        for idx in sorted(self._tool_blocks):
            out += b"".join(self._close_block(tool_index=idx))
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


def _stream_error_type(d: dl.StreamError) -> str:
    """Anthropic error type for a mid-stream failure.

    Claude Code's retry/backoff branch keys on this. Reporting every mid-stream
    failure as ``api_error`` presented an overloaded or rate-limited upstream as
    an opaque error the client would not back off from, even though the IR
    carried the real classification in ``StreamError.kind``/``etype``
    (AUDIT #156).
    """
    # The upstream named its own type (Anthropic error frames carry one):
    # that is the most precise signal and passes through verbatim.
    if d.etype:
        return d.etype
    if d.status == 529:
        return "overloaded_error"
    if d.status == 429:
        return "rate_limit_error"
    if d.kind == "timeout":
        return "timeout_error"
    return "api_error"


def error_body(status: int, etype: str, message: str,
               request_id: str = "") -> dict[str, Any]:
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
    body: dict[str, Any] = {
        "type": "error",
        "error": {"type": amap.get(etype, "api_error"), "message": message}}
    # The real API always echoes the request id in an error body, and Claude
    # Code surfaces it in bug reports; without it a user-reported failure could
    # not be tied to a wiwi request-log row (AUDIT #156).
    if request_id:
        body["request_id"] = request_id
    return body
