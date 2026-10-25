"""OpenAI Responses wire codec: /v1/responses decode + stream events (fsm_responses).

State lives in ``wiwi/server/response_store.py``: the surface loads a stored
response's output items, hands them to :func:`decode_request` as
``previous_output`` (or to :func:`with_history` to rewrite the body), and the
codec stays the pure translation layer. ``store`` and ``previous_response_id``
are documented in docs/API_REFERENCE.md; AUDIT #213 tracked the old gap.
"""

from __future__ import annotations

import json
import time
from typing import Any

import orjson
import structlog

from wiwi.core.context import RequestContext
from wiwi.ir import builtin_tools as bt
from wiwi.ir import translation as tr
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
    # ``file_id`` (the Files API reference) has no IR document carrier, but
    # dropping the whole block silently meant a spec-legal attachment simply
    # vanished. There is no URL/b64 to fetch from, so the honest options are
    # drop-with-nothing or drop-with-a-warning; the IR has no warning channel
    # at decode, so at least keep the file_data/file_url paths working and
    # leave file_id to the message-path image handling (which does carry it).
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


def _effort_str(value: Any) -> str | None:
    """Coerce a client-supplied reasoning effort to ``str`` or drop it.

    ``GenParams.reasoning_effort`` is typed ``str | None`` but is a plain
    dataclass with no runtime enforcement, so a list/dict straight off the wire
    used to reach ``effort_to_thinking_budget``'s dict lookup and 500 the
    request on every Anthropic/Gemini deployment (AUDIT #351).
    """
    return value if isinstance(value, str) else None


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
        # A non-dict schema rides the IR and re-encodes upstream verbatim
        # (response_format.json_schema.schema) — a 400 naming no field the
        # caller sent (AUDIT #359). Coerce like the sibling codecs do for
        # parameters_json_schema; the name like every other text field.
        raw_schema = fmt.get("schema")
        raw_name = fmt.get("name")
        return ir.ResponseFormat(
            type="json_schema",
            json_schema=(raw_schema if isinstance(raw_schema, dict)
                         else {"type": "object"}),
            name=(raw_name if isinstance(raw_name, str) else None),
            strict=fmt.get("strict"))
    if fmt.get("type") == "json_object":
        return ir.ResponseFormat(type="json_object")
    return None


def with_history(body: dict[str, Any],
                 previous: list[dict[str, Any]],
                 previous_input: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Prepend a stored response's items to this request's ``input``.

    ``previous_response_id`` is dropped: the caller's next hop must carry the
    literal history, because the upstream never saw our id.

    ``previous_input`` is the stored turn's **input** half — the user's
    message and any tool results that went with it. It is prepended BEFORE
    the previous turn's output, because that is the order the conversation
    actually happened in: the user asked, the model answered, the user asked
    again.

    Without it the replay carried only the assistant's half, so the model's
    next turn began with an assistant message and no user turn to answer.
    The user never learned that the model was being asked to continue, and
    the model never learned what it was continuing FROM — it answered
    "the same thing again", or refused, or asked for clarification, with no
    error anywhere to explain it (AUDIT #350).
    """
    merged = dict(body)
    merged.pop("previous_response_id", None)
    items: list[dict[str, Any]] = []
    if previous_input:
        items.extend(previous_input)
    items.extend(previous)
    existing = body.get("input")
    if isinstance(existing, str):
        items.append({"type": "message", "role": "user", "content": existing})
    elif isinstance(existing, list):
        items.extend(existing)
    merged["input"] = items
    return merged


def decode_request(body: dict[str, Any],
                   previous_output: list[dict[str, Any]] | None = None) -> ir.Request:
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise DialectError("'model' is required")
    if body.get("previous_response_id") and previous_output is None:
        # Reachable only when the caller bypasses the store (state disabled, or a
        # direct codec call). The surface loads and key-checks the row first and
        # hands the items in as ``previous_output``.
        raise DialectError("previous_response_id is unknown or expired; send full input")

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
    elif raw_input is None:
        items = []
    else:
        # A non-list/non-string input (a dict, 123, true) used to fall through
        # to `items = []`, so the gateway called the upstream with an empty
        # prompt, got a 200, and billed for an answer to a question nobody
        # asked (AUDIT #352). Both sibling codecs reject the same shape:
        # openai_chat.py:77 and anthropic_messages.py:53 raise
        # DialectError("'messages' must be a list"). A missing `input` still
        # means empty (that is how the two shapes above agree).
        raise DialectError("'input' must be a string or a list")

    # Stored history first: the caller's own input is the continuation.
    if previous_output:
        items = list(previous_output) + items

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
                        # ``file_id`` is a spec-legal image reference the
                        # Files API resolves server-side; ImagePart.file_id
                        # carries it so an Anthropic upstream can re-emit it
                        # natively instead of dropping the block.
                        fid = c.get("file_id")
                        if isinstance(fid, str) and fid:
                            parts.append(ir.ImagePart(file_id=fid))
                            continue
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
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                # Mirror the function_call_output guard (AUDIT #331): a call with
                # no id can never be paired with a result, and the client is
                # left holding an unanswerable turn (AUDIT #368).
                raise DialectError("function_call is missing 'call_id'")
            messages.append(ir.Message(role="assistant", parts=[
                ir.ToolUsePart(id=call_id,
                               name=_str_or_empty(item.get("name")),
                               args=_load_args(raw_args), raw_args=raw_args)]))
        elif itype == "function_call_output":
            # Images in a tool result (computer-use screenshots) ride
            # ToolResultPart.images so multimodal adapters can re-emit them.
            # A result with no call_id cannot be correlated to the call it
            # answers: upstream 400s, or worse, attaches it to the wrong
            # call. Reject it here (AUDIT #331) rather than an empty id.
            raw_call_id = item.get("call_id")
            if not isinstance(raw_call_id, str) or not raw_call_id:
                raise DialectError("function_call_output is missing 'call_id'")
            messages.append(ir.Message(role="tool", parts=[
                ir.ToolResultPart(tool_use_id=raw_call_id,
                                  content=_item_text(item),
                                  images=_item_images(item))]))
        elif itype == "reasoning":
            summary = item.get("summary") or []
            if not isinstance(summary, list):
                summary = []  # non-list summary: nothing to flatten, skip it
            text = " ".join(s["text"] for s in summary
                            if isinstance(s, dict) and isinstance(s.get("text"), str))
            # The opaque blob the Responses API demands back on the next
            # turn. Codex sends ``include: ["reasoning.encrypted_content"]``
            # on EVERY request and must replay every output item verbatim,
            # blob included — without it the next turn is rejected with
            # "'encrypted_content' is required" and reasoning-model
            # multi-turn breaks on turn 2 (AUDIT #355). The IR carries it
            # in ``ThinkingPart.encrypted_content``; the item is emitted
            # even when the summary is empty, because the blob alone is
            # what the API requires back.
            blob = item.get("encrypted_content")
            blob = blob if isinstance(blob, str) and blob else None
            if text or blob:
                messages.append(ir.Message(role="assistant", parts=[
                    ir.ThinkingPart(text=text, encrypted_content=blob)]))
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
    raw_ptc = body.get("parallel_tool_calls")
    raw_reasoning = body.get("reasoning")
    g = ir.GenParams(
        temperature=tr.coerce_float(body.get("temperature")),
        top_p=tr.coerce_float(body.get("top_p")),
        max_tokens=ir.coerce_int(body.get("max_output_tokens")),
        # stop is not a documented Responses param, but clients that send it
        # mean the same thing; accept a bare string or a list. Anything else
        # (true, 7, {"a": 1}) is not a stop sequence — `(stop_raw or [])`
        # forwarded it verbatim and the upstream 400'd naming nothing the
        # caller sent (AUDIT #185).
        stop=_stop_list(stop_raw),
        seed=ir.coerce_int(body.get("seed")),
        # Same class as `max_output_tokens` on the line above: a non-int
        # `top_k` ('7', true, {}) was forwarded upstream verbatim (AUDIT #187).
        # The Chat and Anthropic decoders both coerce their equivalents.
        top_k=ir.coerce_int(body.get("top_k")),
        parallel_tool_calls=(raw_ptc if isinstance(raw_ptc, bool) else None),
        disable_parallel_tool_use=(True if raw_ptc is False else None),
        reasoning_effort=_effort_str(raw_reasoning.get("effort")
                                     if isinstance(raw_reasoning, dict) else None),
    )
    g.response_format = _decode_response_format(body.get("text"))
    extras = {k: v for k, v in body.items() if k not in _KNOWN_KEYS}
    # `text` is denylisted above so the whole object could not ride extras, but
    # only `text.format` is read into the IR. `text.verbosity` is on the
    # openai/nim adapters' explicit forward list, so the plumbing to carry it
    # exists — the decoder was discarding it before it got there (AUDIT #369).
    # Flatten it to the bare string the adapter expects, mirroring the Chat
    # surface's `verbosity` field.
    verbosity = (body.get("text") or {}).get("verbosity") \
        if isinstance(body.get("text"), dict) else None
    if isinstance(verbosity, str):
        extras["verbosity"] = verbosity
    # ``reasoning`` is in _KNOWN_KEYS so only ``.effort`` was read; a
    # ``summary`` selection ("detailed"/"none"/"auto") rode nothing at all —
    # neither mapped nor forwarded (the ``text.verbosity`` class, AUDIT #369).
    # The OpenAI adapter has no ``reasoning`` forward arm, so hand it the
    # documented bare-key form the upstream accepts.
    r_summary = raw_reasoning.get("summary") \
        if isinstance(raw_reasoning, dict) else None
    if isinstance(r_summary, str) and r_summary:
        extras["reasoning_summary"] = r_summary
    return ir.Request(model=model, messages=messages, tools=tools,
                      tool_choice=tool_choice, gen_params=g,
                      stream=bool(body.get("stream")),
                      # Unmapped Responses params (prompt_cache_key,
                      # safety_identifier, store, metadata, truncation, ...):
                      # openai_adapter forwards these upstream, so keep them
                      # rather than dropping them on the floor.
                      extras=extras)


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
#
# ``context_window_exceeded`` and ``pause_turn`` are here for the same
# reason: both mean the turn did NOT complete, and both are stop reasons the
# IR carries precisely so the client can react (``ir/types.py`` lists them
# among the values "stop" cannot express). Reporting "completed" for a
# context overflow removed the client's only signal to trigger auto-compact
# — the exact failure the IR field exists to prevent, and the exact symptom
# AUDIT #272 recorded and closed as "non-actionable" (AUDIT #358).
_INCOMPLETE_REASONS = {
    "length": "max_output_tokens",
    "content_filter": "content_filter",
    # A context overflow is a truncation by another name. The spec's own
    # vocabulary for "the turn did not finish" is the reason string, and
    # ``max_output_tokens`` is the closest existing value.
    "context_window_exceeded": "max_output_tokens",
    # A paused turn (the provider yielded control back) is likewise not a
    # completion; the client must be able to tell it apart from one.
    "pause_turn": "pause_turn",
}

# Cap on content buffered while a tool item is open (AUDIT #367). Matches the
# Anthropic encoder's MAX_DEFERRED_CHARS: a model that narrates around a tool
# call must not be able to grow this without bound, and the newest content is
# the one worth keeping.
MAX_DEFERRED_CHARS = 64 * 1024


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

def _reasoning_item(text: str, req_id: str, n: int,
                    encrypted_content: str | None = None) -> dict[str, Any]:
    """A reasoning output item; ``n`` disambiguates multiple thinking blocks.

    ``encrypted_content`` is the opaque blob the Responses API hands back and
    demands back on the next turn. It is emitted only when present: a
    reasoning item with no blob must not grow a fake one, and the field is
    not part of the item's shape for providers that never send it
    (AUDIT #355).
    """
    item: dict[str, Any] = {"type": "reasoning", "id": f"rs_{req_id}_{n}",
                             "summary": [{"type": "summary_text", "text": text}]}
    if encrypted_content:
        item["encrypted_content"] = encrypted_content
    return item


def _message_item_id(req_id: str, n: int) -> str:
    """The output item id for a streamed message item.

    Streaming emits one message item per interleaved text run, so the id has to
    be per-item: a bare ``msg_<req>`` gave a text run before a tool call and the
    one flushed after it the same id, and a client accumulating output items by
    id cannot tell two items with one name apart (AUDIT #375). The non-stream
    path keeps the plain ``msg_<req>`` spelling.
    """
    return f"msg_{req_id}_{n}"


def _message_item(text: str, req_id: str, n: int | None = None) -> dict[str, Any]:
    item_id = _message_item_id(req_id, n) if n is not None else f"msg_{req_id}"
    return {"type": "message", "id": item_id, "status": "completed",
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
        # The blob rides on the item so the next turn can replay it
        # verbatim (AUDIT #355).
        output.append(_reasoning_item(t.text, req_id, out_id,
                                       encrypted_content=t.encrypted_content))
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
                                              json.dumps(t.args)))
        out_id += 1
    u = turn.usage
    # Usage convention re-base: OpenAI totals the prompt INCLUDING cached
    # tokens; an Anthropic-shaped upstream reports input_tokens EXCLUSIVE of
    # cache reads (cached_in_prompt=False). Re-emit in this surface's own
    # convention so an OpenAI client's totals are self-consistent.
    prompt_incl = u.prompt_tokens + (0 if u.cached_in_prompt else u.cached_tokens)
    resp = _response_obj(
        model, req_id, turn.stop_reason, output,
        _usage_obj(prompt_incl, u.completion_tokens, u.cached_tokens,
                   u.reasoning_tokens),
        created_at=int(time.time()))
    # The matched stop sequence (AUDIT #497). The Responses surface has no
    # ``stop_sequence`` field of its own, but the information was on the IR
    # and the caller had no way to learn it: a turn that stopped on a
    # caller-supplied sequence was indistinguishable from one that stopped
    # on its own.
    if turn.stop_sequence:
        resp["stop_sequence"] = turn.stop_sequence
    # Per-token logprobs (AUDIT #326). The Responses API has no logprobs
    # field, but the caller asked for them and the upstream billed for them;
    # dropping them here meant paying for a feature the caller never saw.
    if turn.logprobs is not None:
        resp["logprobs"] = turn.logprobs
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
        # The matched stop sequence from Finish (the sync twin emits it,
        # AUDIT #497); surfaced on the terminal response object.
        self._stop_seq: str | None = None
        # Piece lists rather than repeated `+=`: joining once at close is
        # O(n) total instead of O(n^2) copying for a long stream.
        self._text_buf: list[str] = []
        self._think_buf: list[str] = []
        # The opaque ``encrypted_content`` blob of the open reasoning item,
        # if the delta carried one. Codex demands it back on the next turn,
        # so it must ride on the emitted item (AUDIT #355).
        self._think_blob: str | None = None
        # Per-tool-call state keyed by the IR stream's tool index, so
        # interleaved parallel tool calls (Open(0) Open(1) Args(0) ...) don't
        # corrupt each other's item ids / argument buffers.
        self._tools: dict[int, dict[str, Any]] = {}
        self._open_tool: int | None = None
        # Text/thinking that arrived while a tool item was open. It cannot be
        # emitted inline — a text delta must never close an open tool item, or
        # _close_tool POPS the entry and the tool's later args fragments are
        # dropped mid-call — so it is buffered here and flushed as its own
        # output item on ToolCallClose, exactly as the Anthropic encoder's
        # _defer/_flush_deferred pair does (AUDIT #367).
        #
        # This buffer used to not exist: the encoder returned None and the
        # content was silently discarded, while a comment claimed the policy
        # "mirrors the Anthropic encoder" — which in fact preserves it.
        self._deferred: list[tuple[str, str, str | None]] = []
        self._deferred_chars = 0
        # Streaming logprobs accumulator (AUDIT #326): LogprobsDelta has no
        # Responses event, so the tokens collect here for the terminal object.
        self._logprobs: dict[str, Any] | None = None
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
        """output_item.done — the one event every closed item has in common.

        The item is inserted at its ``output_index``, not appended: the
        delta contract explicitly permits batch closes in ANY order
        ("Adapters may hold several indices open at once and close them in a
        batch"), so the CLOSE order is not the display order. Appending put
        the terminal ``response.completed.output`` array in close order
        while the per-item ``output_item.added``/``done`` events were in
        output_index order, so a client rebuilding the turn from the
        terminal payload got the tool calls in the wrong sequence relative
        to the text they bracket (AUDIT #357).
        """
        # Keep the array dense and ordered: insert at the index, shifting
        # later entries right, and drop anything already parked there.
        while len(self._output) < idx:
            self._output.append({})
        if idx < len(self._output):
            self._output[idx] = item
        else:
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
            out = [self._item_done(idx, item)]
        else:
            # The item id keys on the OUTPUT INDEX, not the IR tool index.
            #
            # ``encode_response`` (the non-streaming twin of this encoder)
            # mints ``fc_{req_id}_{out_id}`` from a counter over every output
            # item — thinking, message and tool alike. The stream used the IR
            # tool index instead, so the same turn carried TWO different ids
            # for the same call: with one message before the tool, the stream
            # said ``fc_r1_0`` and the sync path said ``fc_r1_1``. A client
            # that stored the streamed id and later referenced it against a
            # sync-shaped payload pointed at a different item, and the gap
            # grew with every preceding item (AUDIT #356).
            item_id = f"fc_{self.req_id}_{idx}"
            item = _function_call_item(item_id, t["call_id"], t["name"], t["args"])
            out = [self._evt("response.function_call_arguments.done", {
                        "item_id": item_id, "output_index": idx,
                        "arguments": t["args"]}),
                   self._item_done(idx, item)]
        # Once the LAST open tool is closed the buffer is safe to emit (AUDIT
        # #367). It goes after the tool's own done event, preserving the order
        # the client saw the content in. With a parallel sibling still open the
        # flush waits for it: a message item emitted between the two tools'
        # done events would interleave output items mid-call.
        #
        # This runs for a hosted (builtin) close too — that branch used to
        # return early, so narrative text written around a `web_search_call`
        # was buffered and never emitted (AUDIT #374).
        if not self._tools:
            out.extend(self._flush_deferred())
        return out

    def _defer(self, kind: str, text: str,
               encrypted_content: str | None = None) -> None:
        """Buffer content that arrived while a tool item was open.

        Capped like the Anthropic encoder's ``MAX_DEFERRED_CHARS``: evict from
        the front so the newest content survives, and trim the boundary entry
        from its head rather than dropping it whole, so the buffer always ends
        up within the cap even when one delta is itself larger than it.

        The overshoot handling mirrors the Anthropic encoder's ``_defer``: a
        pop that takes the total *below* the cap put back that entry's tail,
        because the pop removed more than the cap required. Without it a single
        oversized delta was popped whole and the loop then had nothing left to
        trim, so an over-cap chunk emptied the ENTIRE buffer — discarding prior
        in-flight content that was nowhere near the cap (AUDIT #376).

        A blob-only thinking delta (no text) still buffers an entry: the
        ``encrypted_content`` is what Codex must replay next turn (AUDIT
        #355), and the old ``if not text: return`` dropped it entirely.
        """
        if not text and not encrypted_content:
            return
        self._deferred.append((kind, text, encrypted_content))
        total = self._deferred_chars + len(text)
        while total > MAX_DEFERRED_CHARS and self._deferred:
            k, t, b = self._deferred.pop(0)
            total -= len(t)
            if total < MAX_DEFERRED_CHARS:
                keep = MAX_DEFERRED_CHARS - total
                if keep > 0 and t:
                    self._deferred.insert(0, (k, t[-keep:], b))
                    total += keep
                break
        self._deferred_chars = total

    def _flush_deferred(self) -> list[bytes]:
        """Emit buffered content as its own output item(s).

        Only safe when no tool item is open — the buffer exists precisely
        because one was. Consecutive same-kind entries coalesce into one item,
        so text-then-thinking yields two items and thinking-then-text yields
        two, matching how the real API interleaves content around a tool call.
        Emitting one item per *entry* instead produced one item per streamed
        chunk, all sharing an id (AUDIT #375).
        """
        if not self._deferred:
            return []
        # Merge adjacent same-kind entries into runs first, so a text run that
        # the adapter split across deltas becomes a single output item. The
        # LAST blob in a thinking run wins: adapters send one blob per
        # reasoning block, and a run is one block (AUDIT #355).
        runs: list[list[Any]] = []
        for kind, text, blob in self._deferred:
            if runs and runs[-1][0] == kind:
                runs[-1][1] += text
                if blob:
                    runs[-1][2] = blob
            else:
                runs.append([kind, text, blob])
        out: list[bytes] = []
        for kind, text, blob in runs:
            if kind == "thinking":
                out.extend(self._reasoning_item_events(
                    text, encrypted_content=blob))
            else:
                out.extend(self._message_item_events(text))
        self._deferred.clear()
        self._deferred_chars = 0
        return out

    def _reasoning_item_events(self, text: str,
                               encrypted_content: str | None = None) -> list[bytes]:
        """add → summary delta → summary done → item.done for a reasoning item.

        ``encrypted_content`` is the AUDIT #355 blob: when the reasoning was
        buffered behind an open tool item, ThinkingDelta stashed it in
        ``_think_blob`` — and this flush path dropped it, so a Codex turn
        whose thinking interleaved with a tool call replayed WITHOUT its blob
        and the next request was rejected with "'encrypted_content' is
        required". The blob arrives here via _flush_deferred.
        """
        oi = self._next_output_index()
        item_id = f"rs_{self.req_id}_{oi}"
        out = [
            self._evt("response.output_item.added", {
                "output_index": oi,
                "item": {"type": "reasoning", "id": item_id, "summary": []}}),
        ]
        if text:
            out.append(self._evt("response.reasoning_summary_text.delta", {
                "item_id": item_id, "output_index": oi, "delta": text}))
            out.append(self._evt("response.reasoning_summary_text.done", {
                "item_id": item_id, "output_index": oi, "text": text}))
        # _item_done, not a bare event: it records the item so the
        # terminal response.completed output array carries it too.
        out.append(self._item_done(oi, _reasoning_item(
            text, self.req_id, oi, encrypted_content=encrypted_content)))
        return out

    def _message_item_events(self, text: str) -> list[bytes]:
        """add → content_part.added → text.done → content_part.done → item.done."""
        oi = self._next_output_index()
        item_id = _message_item_id(self.req_id, oi)
        return [
            self._evt("response.output_item.added", {
                "output_index": oi,
                "item": {"type": "message", "id": item_id,
                         "status": "in_progress", "role": "assistant",
                         "content": []}}),
            self._evt("response.content_part.added", {
                "item_id": item_id, "output_index": oi,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []}}),
            # Clients (Codex CLI) build the visible text from the delta events;
            # the .done frames alone are not enough to render it.
            self._evt("response.output_text.delta", {
                "item_id": item_id, "output_index": oi,
                "content_index": 0, "delta": text}),
            self._evt("response.output_text.done", {
                "item_id": item_id, "output_index": oi,
                "content_index": 0, "text": text}),
            self._evt("response.content_part.done", {
                "item_id": item_id, "output_index": oi,
                "content_index": 0,
                "part": {"type": "output_text", "text": text, "annotations": []}}),
            self._item_done(oi, _message_item(text, self.req_id, oi)),
        ]

    def _close_item(self) -> list[bytes]:
        if self._item_open is None:
            return []
        kind = self._item_open
        idx = self._open_out
        self._item_open = None
        if kind == "message":
            text = "".join(self._text_buf)
            item_id = _message_item_id(self.req_id, idx)
            part = {"type": "output_text", "text": text, "annotations": []}
            return [self._evt("response.output_text.done", {
                        "item_id": item_id, "output_index": idx,
                        "content_index": 0, "text": text}),
                    self._evt("response.content_part.done", {
                        "item_id": item_id, "output_index": idx,
                        "content_index": 0, "part": part}),
                    self._item_done(idx, _message_item(text, self.req_id, idx))]
        if kind == "thinking":
            think = "".join(self._think_buf)
            # The blob rides on the emitted item: Codex must replay it on
            # the next turn (AUDIT #355).
            item = _reasoning_item(think, self.req_id, idx,
                                   encrypted_content=self._think_blob)
            self._think_blob = None
            out = []
            if think:
                out.append(self._evt("response.reasoning_summary_text.done", {
                            "item_id": item["id"], "output_index": idx,
                            "text": think}))
            out.append(self._item_done(idx, item))
            return out
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
            # half-finished call). Buffer it instead and flush it as its own
            # item when the tool closes — the Anthropic encoder's policy, which
            # this previously claimed to mirror while discarding the text
            # outright (AUDIT #367).
            if self._item_open == "tool":
                self._defer("text", d.text)
                return None
            out = []
            if self._item_open != "message":
                out.extend(self._close_item())
                oi = self._next_output_index()
                item_id = _message_item_id(self.req_id, oi)
                out.append(self._evt("response.output_item.added", {
                    "output_index": oi,
                    "item": {"type": "message", "id": item_id,
                             "status": "in_progress", "role": "assistant",
                             "content": []}}))
                out.append(self._evt("response.content_part.added", {
                    "item_id": item_id, "output_index": oi,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []}}))
                self._item_open = "message"
                self._text_buf = []
            self._text_buf.append(d.text)
            out.append(self._evt("response.output_text.delta", {
                "item_id": _message_item_id(self.req_id, self._open_out),
                "output_index": self._open_out,
                "content_index": 0, "delta": d.text}))
            return b"".join(out)
        if isinstance(d, dl.ThinkingDelta):
            # A blob-only delta carries no summary text but DOES carry the
            # opaque ``encrypted_content`` the next turn must replay, so it
            # must not be dropped (AUDIT #355). The text is optional; the
            # blob alone is enough to open a reasoning item.
            if not d.text and not d.encrypted_content:
                return None  # signature-only delta: no Responses representation
            # Same interleave policy as TextDelta: never close an open tool
            # item from a thinking delta (round-25 proved this class of bug
            # for text; thinking has the identical _close_item hazard). Buffer
            # rather than drop, for the same reason (AUDIT #367).
            if self._item_open == "tool":
                # Blob rides the deferred entry itself: _think_blob is
                # consumed by _close_item, which the flush bypasses (AUDIT
                # #355's interleave hole).
                self._defer("thinking", d.text,
                            encrypted_content=d.encrypted_content)
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
                self._think_blob = None
            self._think_buf.append(d.text)
            if d.encrypted_content:
                self._think_blob = d.encrypted_content
            if not d.text:
                # Blob-only: the item is opened, nothing to stream yet.
                return b"".join(out)
            out.append(self._evt("response.reasoning_summary_text.delta", {
                "item_id": f"rs_{self.req_id}_{self._open_out}",
                "output_index": self._open_out, "delta": d.text}))
            return b"".join(out)
        if isinstance(d, dl.LogprobsDelta):
            # Per-token logprobs (streaming half of AUDIT #326). The sync
            # path lifts them onto the response object; this surface has no
            # per-token event either, so the same treatment: stash for the
            # terminal response.completed payload. Emitting nothing mid-stream
            # keeps the event sequence legal (no phantom item types).
            if d.logprobs:
                self._logprobs = d.logprobs
            return None
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
                # Keyed on the OUTPUT INDEX so the streamed id matches the
                # non-streaming twin's (see _close_tool, AUDIT #356).
                item = _function_call_item(f"fc_{self.req_id}_{oi}", d.id, d.name, "")
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
                "item_id": f"fc_{self.req_id}_{t['output_index']}",
                "output_index": t["output_index"],
                "delta": d.args_fragment})
        if isinstance(d, dl.ToolCallClose):
            return b"".join(self._close_tool(d.index))
        if isinstance(d, dl.UsageFinal):
            self._usage = d
            return None
        if isinstance(d, dl.Finish):
            self._stop = d.stop_reason
            # AUDIT #497, streaming half: the sequence must reach the client,
            # not just the reason that something matched.
            self._stop_seq = d.stop_sequence
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
                             # Same classification as the sync path's
                             # ``error_body``, not a hardcoded ``api_error``:
                             # a mid-stream 429/529/timeout says what it is
                             # (AUDIT #478).
                             "error": {"code": dl.stream_error_type(d),
                                       "message": d.message}}})
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
        # NB: the Responses surface does NOT publish a `tool_call` stop reason
        # — its turn shape is the output item array plus `status`, and
        # `_response_obj` consults the reason only for the truncation mapping.
        # Adding a tool-call validity guard here would be inert, so the
        # #271-class defect simply does not exist on this surface. Left as-is
        # deliberately rather than shipping a check that changes nothing.
        # Truncation is a DISTINCT terminal event (response.incomplete) with
        # status "incomplete" + incomplete_details — not a completed response.
        # The test must be the SAME table _response_obj keys the payload's
        # ``status`` off: ``length`` is not the only incomplete stop, and
        # keying the event type on it alone emitted ``response.completed``
        # wrapping a payload that said ``"status": "incomplete"`` for a
        # content-filtered turn. A client switching on the event type (the
        # OpenAI SDK, Codex CLI) saw a clean completion and never learned the
        # filter had cut the answer, while the non-streaming encoder — which
        # consults the table — reported it correctly (AUDIT #477).
        incomplete = self._stop in _INCOMPLETE_REASONS
        etype = "response.incomplete" if incomplete else "response.completed"
        # Usage convention re-base (the sync twin's rule): OpenAI totals the
        # prompt INCLUDING cached tokens; an Anthropic-shaped upstream reports
        # input_tokens EXCLUSIVE of cache reads.
        prompt_incl = u.prompt + (0 if u.cached_in_prompt else u.cached)
        resp = _response_obj(self.model, self.req_id, self._stop, self._output,
                             _usage_obj(prompt_incl, u.output, u.cached, u.reasoning))
        # The matched stop sequence (AUDIT #497): the terminal object is where
        # a Responses client reads it (the sync twin sets the same key).
        if self._stop_seq:
            resp["stop_sequence"] = self._stop_seq
        # Streaming logprobs (AUDIT #326): no per-token event exists on this
        # surface, so the collected tokens ride the terminal object — the
        # same key the sync path emits.
        if self._logprobs is not None:
            resp["logprobs"] = self._logprobs
        return closing + self._evt(etype, {"response": resp})


def error_body(status: int, etype: str, message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": etype, "code": etype,
                      "param": None}}
