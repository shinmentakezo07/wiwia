"""Anthropic adapter: Messages API encode/decode incl. SSE event folding."""

from __future__ import annotations

import json
from typing import Any

import orjson
import structlog

from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.streaming import deltas as dl

log = structlog.get_logger("wiwi.anthropic_adapter")

DEFAULT_MAX_TOKENS = 4096
MIN_THINKING_BUDGET = 1024  # Anthropic API minimum for budget_tokens

# Prompt-cache breakpoint injection (opt-in via the ``prompt_cache``
# deployment param). Anthropic's minimum cacheable prefix is model-dependent
# (512 for the newest Opus/Fable/Mythos tiers, 1024 for Sonnet 4.x/4.5/4.6,
# 4096 for Opus 4.5/4.6 and Haiku 4.5). Below the minimum the API silently
# does NOT cache, so marking a short prefix is a write that will never be
# read. 1024 is the conservative common denominator: it is the *lowest*
# threshold that still covers most Sonnet/Opus deployments without marking
# prefixes that cannot possibly be cached on Haiku or Opus 4.5/4.6.
DEFAULT_PROMPT_CACHE_MIN_TOKENS = 1024

# Anthropic 2026 top-level params the wire codec captures into req.extras;
# safe to forward verbatim to the Messages API (mirrors openai_adapter's
# _STANDARD under drop_params=True).
_ANTHROPIC_STANDARD = {
    "service_tier", "speed", "metadata", "mcp_servers", "container",
    "context_management", "fallbacks", "cache_control",
}

# Anthropic stop_reason -> IR StopReason. Only ``end_turn`` is a true "the model
# finished"; the rest carry information a client acts on. Collapsing
# ``pause_turn`` (a provider-hosted tool loop is mid-flight; re-send to
# continue), ``stop_sequence`` (which caller-supplied sequence fired) and
# ``model_context_window_exceeded`` (an overflow, not a completion) into
# ``stop`` made Claude Code end server-tool turns early and left auto-compact
# unable to see an overflow (AUDIT #156).
_STOP_REASON_IN: dict[str, str] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "tool_call",
    "stop_sequence": "stop_sequence",
    "refusal": "content_filter",
    "pause_turn": "pause_turn",
    "model_context_window_exceeded": "context_window_exceeded",
    "compaction": "compaction",
}


def _system_blocks_or_text(messages: list[ir.Message]) -> str | list[dict[str, Any]] | None:
    """System prompt for the Messages API. Preserves cache_control by emitting
    block form when any part carries it (this is what enables Anthropic prompt
    caching); plain string otherwise.

    Only LEADING system messages are hoisted: a ``system`` entry that follows a
    user/assistant turn is a mid-conversation system message, which the API
    accepts inside ``messages`` and whose position matters to the cache prefix.

    Empty text blocks are dropped. Anthropic rejects them ("text content blocks
    must be non-empty"), and a client may legitimately send one — the message
    path already filtered them but this path did not, so a single empty block
    in the ``system`` array was a hard 400 (AUDIT #156).
    """
    text_parts: list[ir.TextPart] = []
    for m in messages:
        if m.role != "system":
            break  # everything after the first non-system turn stays in messages
        text_parts.extend(p for p in m.parts
                          if isinstance(p, ir.TextPart) and p.text)
    if not text_parts:
        return None
    if any(p.cache_control for p in text_parts):
        blocks: list[dict[str, Any]] = []
        for p in text_parts:
            b: dict[str, Any] = {"type": "text", "text": p.text}
            if p.cache_control:
                b["cache_control"] = p.cache_control
            blocks.append(b)
        return blocks
    return "\n".join(p.text for p in text_parts)


_JSON_ONLY_INSTRUCTION = (
    "Respond with a single valid JSON object and nothing else — no prose, "
    "no markdown code fences, no commentary before or after the JSON."
)


def _json_schema_instruction(schema: dict[str, Any] | None) -> str:
    """Instruction telling the model to match a caller-supplied JSON schema.

    The Messages API has no ``response_format`` parameter, so a request that
    arrives in the OpenAI dialect (``json_object`` / ``json_schema``) would
    otherwise be silently dropped and the caller would get prose back.
    """
    if not schema:
        return _JSON_ONLY_INSTRUCTION
    try:
        rendered = orjson.dumps(schema).decode()
    except (TypeError, ValueError):
        return _JSON_ONLY_INSTRUCTION
    return (_JSON_ONLY_INSTRUCTION
            + " The JSON object must conform to this JSON Schema:\n" + rendered)


def _with_response_format_instruction(
    system: str | list[dict[str, Any]] | None,
    response_format: ir.ResponseFormat | None,
) -> str | list[dict[str, Any]] | None:
    """Append the JSON-output instruction to the system prompt.

    Anthropic has no native ``response_format``, so the constraint is carried
    as a system instruction. When the caller already sent a system prompt the
    instruction is appended rather than replacing it; when the prompt was in
    block form (``cache_control`` present) it is appended as a plain trailing
    block so the cached prefix stays byte-identical.
    """
    if response_format is None or response_format.type == "text":
        return system
    instruction = _json_schema_instruction(response_format.json_schema)
    if system is None:
        return instruction
    if isinstance(system, str):
        return system + "\n" + instruction
    # Block form: don't mutate the caller's blocks (that would invalidate the
    # cache prefix); append a separate uncached block.
    return list(system) + [{"type": "text", "text": instruction}]


def _prefix_token_estimate(req: ir.Request) -> int:
    """Rough token count of the *stable* prefix: tools + system only.

    Deliberately excludes user/assistant turns. Those are the part that
    varies per request, so they must not count toward the "is this worth
    caching" decision — a long final user message does not make the tools
    and system prompt any more cacheable.

    Uses the same chars/4 heuristic as the rest of the codebase rather than
    tiktoken: this runs synchronously inside ``encode_request`` on the hot
    path, and it only needs to be right to within a factor of two.
    """
    chars = 0
    for t in req.tools:
        chars += len(t.name or "")
        chars += len(t.description or "")
        try:
            chars += len(orjson.dumps(t.parameters_json_schema or {}))
        except (TypeError, ValueError):
            pass
    for m in req.messages:
        if m.role != "system":
            continue
        for p in m.parts:
            if isinstance(p, ir.TextPart):
                chars += len(p.text)
    return max(1, chars // 4)


def _count_breakpoints(req: ir.Request) -> int:
    """Breakpoints the caller already placed, so we do not exceed 4."""
    n = 0
    for t in req.tools:
        if t.cache_control:
            n += 1
    for m in req.messages:
        for p in m.parts:
            if getattr(p, "cache_control", None):
                n += 1
    return n


class AnthropicAdapter:
    provider_type = "anthropic"

    def __init__(self) -> None:
        self._tool_indices: set[int] = set()
        # Usage fields seen at message_start; consumed at message_delta. Held on
        # the instance because the two SSE events arrive in separate calls.
        self._pending_prompt = 0
        self._pending_cached = 0
        self._pending_cache_creation = 0

    def reset(self) -> None:
        """Drop per-stream state so the adapter can serve another stream."""
        self._tool_indices.clear()
        self._pending_prompt = 0
        self._pending_cached = 0
        self._pending_cache_creation = 0

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        return {"x-api-key": key.secret, "anthropic-version": "2023-06-01"}

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        base = base_url.rstrip("/")
        return f"{base}/messages"

    def _should_inject(self, req: ir.Request, params: dict[str, Any],
                       body: dict[str, Any] | None = None) -> bool:
        """Whether breakpoint injection should run for this request."""
        if not params.get("prompt_cache"):
            return False
        # Caller knows best: if it placed any breakpoints, don't second-guess.
        # Bailing out entirely (rather than topping up to 4) keeps us from
        # mixing our guesses with deliberate markers.
        if _count_breakpoints(req) or (body and "cache_control" in body):
            return False
        min_tokens = params.get("prompt_cache_min_tokens")
        if not isinstance(min_tokens, int) or min_tokens <= 0:
            min_tokens = DEFAULT_PROMPT_CACHE_MIN_TOKENS
        est = _prefix_token_estimate(req)
        if est < min_tokens:
            log.debug("prompt_cache_skipped_prefix_too_short",
                      estimate=est, min_tokens=min_tokens)
            return False
        return True

    def _inject_cache_breakpoints(self, req: ir.Request, body: dict[str, Any],
                                  params: dict[str, Any]) -> None:
        """Mark the stable prefix for Anthropic prompt caching (opt-in).

        Why the prefix and not the whole prompt: a cache write happens ONLY
        at the breakpoint, and a read walks back looking for entries *prior
        requests wrote*. Marking the trailing user turn — which differs every
        request — means every call writes a fresh entry and none ever reads
        one, so you pay the 1.25x write premium forever. Anthropic's own
        top-level "automatic caching" has the same flaw for a
        static-system + varying-message prompt, because it places the
        breakpoint on the last cacheable block.

        So: mark the last tool definition and the last system block, never the
        final user turn.

        Opt-in (``prompt_cache``) because this trades a 1.25x write on the
        first call for 0.1x reads afterwards — only worth it when the prefix
        is genuinely reused.
        """
        if not self._should_inject(req, params, body):
            return
        system = body.get("system")
        if system and isinstance(system, list):
            # _with_response_format_instruction appends its instruction as a
            # separate trailing block, so the last block here is either the
            # caller's own system text (stable) or that instruction (which
            # varies with response_format). Mark the last block that is not
            # the instruction.
            idx = len(system) - 1
            if idx > 0 and _JSON_ONLY_INSTRUCTION in system[idx].get("text", ""):
                # Last block is the appended JSON-output instruction, which
                # varies with response_format — step back to the caller's own
                # (stable) system text.
                idx -= 1
            if idx >= 0 and not system[idx].get("cache_control"):
                system[idx]["cache_control"] = {"type": "ephemeral"}

        tools = body.get("tools")
        if tools and isinstance(tools, list):
            last = tools[-1]
            if not last.get("cache_control"):
                last["cache_control"] = {"type": "ephemeral"}

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        g = req.gen_params
        system = _system_blocks_or_text(req.messages)
        # When injecting cache breakpoints, force the block form up front so
        # the JSON-output instruction (appended below) lands in its OWN
        # trailing block. If it were concatenated into a single string and
        # that string marked, the schema-derived instruction would become part
        # of the cached prefix — and it varies with response_format, so the
        # prefix would stop being stable.
        if isinstance(system, str) and self._should_inject(req, deployment_params):
            system = [{"type": "text", "text": system}]
        msgs: list[dict[str, Any]] = []
        # ``_system_blocks_or_text`` hoists EVERY system message into the
        # top-level ``system`` field, which is wrong for a mid-conversation
        # system entry: the Messages API accepts ``role: "system"`` inside
        # ``messages`` (mid-conversation-system beta) and moving it to the top
        # changes its position in the cache prefix. Only leading system
        # messages are hoisted; a system entry that follows a non-system
        # message stays in the message list.
        seen_non_system = False
        for m in req.messages:
            if m.role == "system" and not seen_non_system:
                continue
            seen_non_system = True
            blocks: list[dict[str, Any]] = []
            for p in m.parts:
                if isinstance(p, ir.TextPart):
                    if not p.text:
                        # Anthropic 400s on empty text blocks ("text content
                        # blocks must be non-empty"); OpenAI-dialect assistant
                        # turns echo content:"" alongside tool_calls.
                        continue
                    b: dict[str, Any] = {"type": "text", "text": p.text}
                    if p.cache_control:
                        b["cache_control"] = p.cache_control
                    blocks.append(b)
                elif isinstance(p, ir.ImagePart):
                    if p.file_id:
                        src = {"type": "file", "file_id": p.file_id}
                    elif p.url:
                        src = {"type": "url", "url": p.url}
                    else:
                        src = {"type": "base64", "media_type": p.mime, "data": p.b64}
                    img: dict[str, Any] = {"type": "image", "source": src}
                    if p.cache_control:
                        img["cache_control"] = p.cache_control
                    blocks.append(img)
                elif isinstance(p, ir.DocumentPart):
                    doc: dict[str, Any]
                    if p.url:
                        doc = {"type": "document",
                               "source": {"type": "url", "url": p.url}}
                    else:
                        doc = {"type": "document",
                               "source": {"type": "base64", "media_type": p.mime,
                                          "data": p.b64}}
                    if p.name:
                        doc["title"] = p.name
                    if p.context:
                        doc["context"] = p.context
                    if p.cache_control:
                        doc["cache_control"] = p.cache_control
                    blocks.append(doc)
                elif isinstance(p, ir.ToolUsePart):
                    # Replay the block type the client actually sent. A
                    # provider-hosted call (server_tool_use) or an MCP call
                    # (mcp_tool_use) must come back as that same type, or its
                    # paired result block is unpaired and Anthropic rejects the
                    # history. ``block_type`` carries the original spelling;
                    # the name check is only a fallback for IR built without it
                    # (AUDIT #156).
                    utype = p.block_type or ("server_tool_use"
                                             if bt.is_builtin_name(p.name)
                                             else "tool_use")
                    blocks.append({"type": utype, "id": p.id, "name": p.name,
                                   "input": p.args})
                elif isinstance(p, ir.ToolResultPart):
                    # block_type preserves the inbound result-block type
                    # (web_search_tool_result, ...) so replayed server-tool
                    # history keeps its original shape.
                    tr: dict[str, Any] = {"type": p.block_type,
                                          "tool_use_id": p.tool_use_id,
                                          "content": p.content}
                    if p.is_error:
                        tr["is_error"] = True
                    if p.cache_control:
                        tr["cache_control"] = p.cache_control
                    if p.images:
                        # Multimodal tool result: block-form content
                        # (text + image blocks) instead of a bare string.
                        content_blocks: list[dict[str, Any]] = []
                        if p.content:
                            content_blocks.append({"type": "text", "text": p.content})
                        for img in p.images:
                            if img.file_id:
                                src = {"type": "file", "file_id": img.file_id}
                            elif img.url:
                                src = {"type": "url", "url": img.url}
                            else:
                                src = {"type": "base64", "media_type": img.mime,
                                       "data": img.b64}
                            content_blocks.append({"type": "image", "source": src})
                        tr["content"] = content_blocks
                    blocks.append(tr)
                elif isinstance(p, ir.ThinkingPart):
                    if p.block_type == "redacted_thinking" and p.data is not None:
                        # Encrypted thinking: replay verbatim. Anthropic
                        # requires the block back on the next turn; dropping
                        # it breaks tool-use continuity.
                        blocks.append({"type": "redacted_thinking", "data": p.data})
                        continue
                    tb: dict[str, Any] = {"type": "thinking", "thinking": p.text}
                    if p.signature:
                        tb["signature"] = p.signature
                    blocks.append(tb)
            if m.role == "assistant":
                role = "assistant"
            elif m.role == "system":
                # Mid-conversation system entry: the API accepts this role in
                # ``messages`` and its position matters to the cache prefix, so
                # pass it through rather than folding it into the user turn.
                role = "system"
            else:
                # user, and tool (tool results ride in a user turn)
                role = "user"
            if blocks:
                if msgs and msgs[-1]["role"] == role:
                    msgs[-1]["content"].extend(blocks)
                else:
                    msgs.append({"role": role, "content": blocks})

        # ``max_tokens: 0`` is Anthropic's documented cache pre-warm signal: the
        # API reads the prompt, writes the cache and returns with zero output
        # tokens. A falsy-or chain turned that into DEFAULT_MAX_TOKENS, so the
        # pre-warm generated and BILLED up to 4096 output tokens and returned a
        # real completion instead of an empty one (AUDIT #156). Test for None
        # explicitly; only a genuinely absent value falls through.
        if g.max_tokens is not None:
            max_tokens = g.max_tokens
        else:
            max_tokens = deployment_params.get("max_tokens") or DEFAULT_MAX_TOKENS
        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": msgs,
        }
        # Structured outputs: json_schema rides natively as output_config.format
        # (2026 GA shape, no beta header); json_object has no native equivalent
        # and stays a system-prompt instruction.
        if g.response_format is not None and g.response_format.type == "json_schema":
            fmt: dict[str, Any] = {
                "type": "json_schema",
                "schema": g.response_format.json_schema or {"type": "object"},
            }
            if g.response_format.name is not None:
                fmt["name"] = g.response_format.name
            if g.response_format.strict is not None:
                fmt["strict"] = g.response_format.strict
            body["output_config"] = {"format": fmt}
        else:
            system = _with_response_format_instruction(system, g.response_format)
        # output_config.effort is Anthropic's own effort knob (Claude Code's
        # /effort, --effort, CLAUDE_CODE_EFFORT_LEVEL). It rides verbatim rather
        # than through the effort→budget map, because the API has a native
        # field for it. Merge into the same object as ``format`` so a request
        # setting both sends one output_config.
        if g.effort:
            body.setdefault("output_config", {})["effort"] = g.effort
        if system:
            body["system"] = system
        if g.temperature is not None:
            body["temperature"] = g.temperature
        if g.top_p is not None:
            body["top_p"] = g.top_p
        if g.top_k is not None:
            body["top_k"] = g.top_k
        if g.stop:
            body["stop_sequences"] = g.stop

        # Thinking configuration.  Explicit modes (2026): adaptive is
        # model-driven with no budget_tokens; disabled omits thinking entirely.
        # Otherwise 'none' effort explicitly disables thinking; a
        # thinking_budget or any other effort level enables it.  The Anthropic
        # API requires budget_tokens >= 1024 and max_tokens > budget_tokens, so
        # we clamp the budget and raise max_tokens to satisfy that invariant.
        if g.thinking_type == "adaptive":
            body["thinking"] = {"type": "adaptive"}
        elif g.thinking_type == "disabled":
            pass  # no thinking key
        else:
            thinking_enabled = (g.thinking_budget is not None
                                or (g.reasoning_effort is not None
                                    and g.reasoning_effort != "none"))
            # A zero budget means "thinking disabled" (the inverse of
            # effort_to_thinking_budget("none") is None, and
            # thinking_budget_to_effort(0) == "none"). Before the zero guard
            # this fell through to the clamp below, turning an explicit
            # disable into thinking ON at the 1024 minimum — the opposite of
            # what the caller asked for (Gemini already honors budget 0 as
            # thinkingBudget: 0).
            if g.thinking_budget == 0:
                thinking_enabled = False
            if thinking_enabled:
                budget = g.effective_thinking_budget()
                if budget is None:
                    # No resolvable budget (unknown effort string, or effort
                    # "none" racing a thinking_budget) — thinking cannot be
                    # configured, so leave it OFF. Defaulting to "medium"
                    # silently billed thinking tokens a caller never asked
                    # for (and switched thinking on for a typo effort).
                    thinking_enabled = False
                else:
                    # Clamp to the API minimum
                    budget = max(budget, MIN_THINKING_BUDGET)
                    # max_tokens must be strictly greater than budget_tokens
                    if body["max_tokens"] <= budget:
                        body["max_tokens"] = budget + 1024
                    body["thinking"] = {"type": "enabled", "budget_tokens": budget}

        # Extras: keys the Anthropic codec recognized as this provider's own
        # 2026 surface ride through; anything else only under drop_params=False
        # (mirrors the OpenAI adapter's policy).
        for k, v in req.extras.items():
            if k in _ANTHROPIC_STANDARD or not deployment_params.get("drop_params", True):
                body.setdefault(k, v)

        if req.tools:
            rendered: list[dict[str, Any]] = []
            for t in req.tools:
                if t.builtin is None:
                    # Function tool with the optional properties Anthropic
                    # supports: strict (OpenAI structured-output strictness),
                    # input_examples (Anthropic-specific), cache_control
                    # (prompt-cache breakpoint on the tool def).
                    entry = {"name": t.name, "description": t.description,
                             "input_schema": t.parameters_json_schema}
                    if t.strict is not None:
                        entry["strict"] = t.strict
                    if t.input_examples is not None:
                        entry["input_examples"] = t.input_examples
                    if t.cache_control is not None:
                        entry["cache_control"] = t.cache_control
                    rendered.append(entry)
                    continue
                wt = bt.wire_type_for("anthropic", t.builtin)
                if wt is None:
                    # Unhostable on Anthropic (e.g. code_execution from another
                    # surface, or an unknown server tool): drop, don't mangle.
                    log.warning("dropping_unhostable_builtin_tool",
                                builtin=t.builtin, provider="anthropic")
                    continue
                # Native server-tool shape: type + canonical name + the config
                # keys Anthropic understands. search_context_size has no
                # Anthropic equivalent (count vs context budget) — dropped.
                entry = {"type": wt, "name": t.name or t.builtin}
                cfg = t.builtin_config or {}
                for k in ("max_uses", "allowed_domains", "blocked_domains",
                          "user_location"):
                    if k in cfg:
                        entry[k] = cfg[k]
                if t.cache_control is not None:
                    entry["cache_control"] = t.cache_control
                rendered.append(entry)
            if rendered:
                body["tools"] = rendered
            tc = req.tool_choice
            disable = g.disable_parallel_tool_use
            if isinstance(tc, ir.ToolChoiceNone):
                tc_obj: dict[str, Any] = {"type": "none"}
            elif isinstance(tc, ir.ToolChoiceAuto):
                tc_obj = {"type": "auto"}
            elif isinstance(tc, ir.ToolChoiceRequired):
                tc_obj = {"type": "any"}
            elif isinstance(tc, ir.ToolChoiceNamed):
                tc_obj = {"type": "tool", "name": tc.name}
            else:
                tc_obj = None
            if tc_obj is not None:
                if disable is not None:
                    tc_obj["disable_parallel_tool_use"] = disable
                body["tool_choice"] = tc_obj
            elif disable is not None:
                # No explicit tool_choice, but disable_parallel_tool_use was set.
                # Anthropic requires it inside a tool_choice object; use the
                # default "auto" as the carrier.
                body["tool_choice"] = {"type": "auto",
                                       "disable_parallel_tool_use": disable}
        if req.stream:
            body["stream"] = True
        # Last: needs body["tools"] and body["system"] fully rendered.
        self._inject_cache_breakpoints(req, body, deployment_params)
        return body

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = orjson.loads(body)
        turn = ir.AssistantTurn(raw=data)
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                raw = block.get("text", "")
                turn.text += raw if isinstance(raw, str) else ""
            elif btype == "thinking":
                rt = block.get("thinking", "")
                turn.thinking.append(ir.ThinkingPart(
                    rt if isinstance(rt, str) else "", block.get("signature")))
            elif btype == "redacted_thinking":
                rd = block.get("data", "")
                turn.thinking.append(ir.ThinkingPart(
                    text="", block_type="redacted_thinking",
                    data=rd if isinstance(rd, str) else ""))
            elif btype == "tool_use":
                turn.tool_calls.append(ir.ToolUsePart(
                    id=block.get("id", ""), name=block.get("name", ""),
                    args=block.get("input") or {}))
            elif btype == "server_tool_use":
                # Anthropic-built-in tools (web_search, code_execution, an MCP
                # tool, ...). These look like tool_use to the caller, but their
                # results arrive as *_tool_result blocks in the same response
                # and the CLIENT never dispatched them. Tag with ``builtin`` so
                # the Anthropic encoder suppresses the block instead of
                # emitting a phantom tool call Claude Code cannot execute
                # (it would try, find no such tool, and 400 on the next turn).
                # The streaming path has tagged these since the builtin work;
                # the sync path did not, so identical upstream output produced
                # a clean stream and a phantom call depending on `stream`
                # (AUDIT #156).
                turn.tool_calls.append(ir.ToolUsePart(
                    id=block.get("id", ""), name=block.get("name", ""),
                    args=block.get("input") or {},
                    builtin=(block.get("name") or "server_tool")))
        sr = data.get("stop_reason", "end_turn")
        turn.stop_reason = _STOP_REASON_IN.get(sr, "stop")
        turn.stop_sequence = data.get("stop_sequence")
        u = data.get("usage") or {}
        # output_tokens_details.thinking_tokens is where Anthropic reports
        # reasoning tokens (newer API); fall back to 0 when absent.
        out_details = u.get("output_tokens_details") or {}
        turn.usage = ir.Usage(
            prompt_tokens=u.get("input_tokens", 0),
            completion_tokens=u.get("output_tokens", 0),
            cached_tokens=u.get("cache_read_input_tokens", 0),
            cache_creation_tokens=u.get("cache_creation_input_tokens", 0),
            reasoning_tokens=out_details.get("thinking_tokens", 0),
        )
        return turn

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        try:
            payload = orjson.loads(data)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            # A non-dict frame (null/number/string/array) must be ignored, not
            # crash on ``payload.get``. The AttributeError escaped into the
            # pump's generic handler, which cooled a healthy deployment and
            # fed the key's retirement ladder for a frame carrying no semantic
            # content (AUDIT #153 — the same frame class #110/#136 fixed for
            # the openai/gemini adapters).
            return []
        etype = payload.get("type", event)
        out: list[dl.IRStreamDelta] = []
        # Typed-wrong nested fields are coerced to their empty shape rather
        # than crashing (AUDIT #153): ``payload.get("message", {})`` only
        # defaults a *missing* key, not a null/typed-wrong value.
        m = payload.get("message")
        if etype == "message_start":
            m = m if isinstance(m, dict) else {}
            u = m.get("usage")
            u = u if isinstance(u, dict) else {}
            self._pending_prompt = u.get("input_tokens", 0)
            self._pending_cached = u.get("cache_read_input_tokens", 0)
            self._pending_cache_creation = u.get("cache_creation_input_tokens", 0)
            # Anthropic reports prompt/cache usage HERE, before any content,
            # and Claude Code drives its context meter and auto-compact
            # decision off this value. Carry it on StreamStart so the encoder
            # can emit it in message_start instead of zeros (AUDIT #156).
            out.append(dl.StreamStart(
                model=m.get("model", ""),
                prompt=self._pending_prompt,
                cached=self._pending_cached,
                cache_creation=self._pending_cache_creation))
        elif etype == "content_block_start":
            cb = payload.get("content_block")
            cb = cb if isinstance(cb, dict) else {}
            idx = payload.get("index", 0)
            if cb.get("type") == "redacted_thinking":
                # Anthropic's extended-thinking redaction: an opaque encrypted
                # blob that MUST be replayed verbatim on the next turn or the
                # provider rejects the history. The non-streaming decoder
                # already preserves it; the streaming path dropped it because
                # only tool blocks were recognized (AUDIT #103). Carry it as a
                # redacted ThinkingDelta so the encoder re-emits the block.
                out.append(dl.ThinkingDelta(
                    text="", block_type="redacted_thinking",
                    data=cb.get("data", "")))
            elif cb.get("type") in ("tool_use", "server_tool_use"):
                self._tool_indices.add(idx)
                # server_tool_use = provider-hosted builtin call (web_search,
                # code_execution, ...): tag the delta so downstream encoders
                # suppress (A1) or re-render as a hosted item instead of a
                # phantom function call. Any server_tool_use block is
                # provider-executed by definition, known to the registry or not.
                is_server = cb.get("type") == "server_tool_use"
                out.append(dl.ToolCallOpen(
                    index=idx, id=cb.get("id", ""), name=cb.get("name", ""),
                    builtin=(cb.get("name") or "server_tool") if is_server else None))
        elif etype == "content_block_delta":
            d = payload.get("delta")
            d = d if isinstance(d, dict) else {}
            dtype = d.get("type")
            if dtype == "text_delta":
                raw = d.get("text", "")
                out.append(dl.TextDelta(raw if isinstance(raw, str) else ""))
            elif dtype == "thinking_delta":
                rt = d.get("thinking", "")
                out.append(dl.ThinkingDelta(rt if isinstance(rt, str) else ""))
            elif dtype == "signature_delta":
                out.append(dl.ThinkingDelta("", signature=d.get("signature")))
            elif dtype == "input_json_delta":
                out.append(dl.ToolCallArgsDelta(index=payload.get("index", 0),
                                                args_fragment=d.get("partial_json", "")))
        elif etype == "content_block_stop":
            # only tool_use blocks close a tool call; text/thinking stops are not
            # tool-call lifecycle events
            idx = payload.get("index", 0)
            if idx in self._tool_indices:
                self._tool_indices.discard(idx)
                out.append(dl.ToolCallClose(index=idx))
        elif etype == "message_delta":
            d = payload.get("delta")
            d = d if isinstance(d, dict) else {}
            u = payload.get("usage")
            u = u if isinstance(u, dict) else {}
            sr = d.get("stop_reason", "end_turn")
            out_details = u.get("output_tokens_details")
            out_details = out_details if isinstance(out_details, dict) else {}
            out.append(dl.UsageFinal(
                prompt=getattr(self, "_pending_prompt", 0),
                cached=getattr(self, "_pending_cached", 0),
                cache_creation=getattr(self, "_pending_cache_creation", 0),
                reasoning=out_details.get("thinking_tokens", 0),
                output=u.get("output_tokens", 0)))
            out.append(dl.Finish(_STOP_REASON_IN.get(sr, "stop"),
                                 stop_sequence=d.get("stop_sequence")))
        elif etype == "message_stop":
            out.append(dl.StreamEnd())
        elif etype == "error":
            err = payload.get("error")
            err = err if isinstance(err, dict) else {}
            etype_val = err.get("type")
            out.append(dl.StreamError(
                message=err.get("message", "unknown anthropic error"),
                kind="status",
                etype=etype_val if isinstance(etype_val, str) else None))
        return out

