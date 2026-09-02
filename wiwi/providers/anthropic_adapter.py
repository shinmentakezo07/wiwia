"""Anthropic adapter: Messages API encode/decode incl. SSE event folding."""

from __future__ import annotations

import json
from typing import Any

import orjson

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.streaming import deltas as dl

DEFAULT_MAX_TOKENS = 4096
MIN_THINKING_BUDGET = 1024  # Anthropic API minimum for budget_tokens

# Anthropic 2026 top-level params the wire codec captures into req.extras;
# safe to forward verbatim to the Messages API (mirrors openai_adapter's
# _STANDARD under drop_params=True).
_ANTHROPIC_STANDARD = {
    "service_tier", "speed", "metadata", "mcp_servers", "container",
    "context_management", "fallbacks", "cache_control",
}


def _system_text(messages: list[ir.Message]) -> str:
    parts: list[str] = []
    for m in messages:
        if m.role == "system":
            parts.extend(p.text for p in m.parts if isinstance(p, ir.TextPart))
    return "\n".join(parts)


def _system_blocks_or_text(messages: list[ir.Message]) -> str | list[dict[str, Any]] | None:
    """System prompt for the Messages API. Preserves cache_control by emitting
    block form when any part carries it (this is what enables Anthropic prompt
    caching); plain string otherwise."""
    text_parts = [p for m in messages if m.role == "system"
                  for p in m.parts if isinstance(p, ir.TextPart)]
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

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        g = req.gen_params
        system = _system_blocks_or_text(req.messages)
        msgs: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role == "system":
                continue
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
                    blocks.append({"type": "image", "source": src})
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
                    blocks.append(doc)
                elif isinstance(p, ir.ToolUsePart):
                    blocks.append({"type": "tool_use", "id": p.id, "name": p.name,
                                   "input": p.args})
                elif isinstance(p, ir.ToolResultPart):
                    tr: dict[str, Any] = {"type": "tool_result",
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
                    tb: dict[str, Any] = {"type": "thinking", "thinking": p.text}
                    if p.signature:
                        tb["signature"] = p.signature
                    blocks.append(tb)
            role = "assistant" if m.role == "assistant" else "user"
            if m.role == "tool":
                # tool results ride in a user turn
                role = "user"
            if blocks:
                if msgs and msgs[-1]["role"] == role:
                    msgs[-1]["content"].extend(blocks)
                else:
                    msgs.append({"role": role, "content": blocks})

        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": g.max_tokens or deployment_params.get("max_tokens") or DEFAULT_MAX_TOKENS,
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
            if thinking_enabled:
                budget = g.effective_thinking_budget()
                if budget is None:
                    budget = ir.effort_to_thinking_budget("medium")
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
            body["tools"] = [
                {"name": t.name, "description": t.description,
                 "input_schema": t.parameters_json_schema}
                for t in req.tools
            ]
            # Forward optional tool properties that Anthropic supports.
            # strict: OpenAI structured-output strictness maps directly.
            # input_examples: Anthropic-specific, helps Claude call tools.
            # cache_control: Anthropic prompt-cache breakpoint on the tool def.
            for i, t in enumerate(req.tools):
                if t.strict is not None:
                    body["tools"][i]["strict"] = t.strict
                if t.input_examples is not None:
                    body["tools"][i]["input_examples"] = t.input_examples
                if t.cache_control is not None:
                    body["tools"][i]["cache_control"] = t.cache_control
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
        return body

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = orjson.loads(body)
        turn = ir.AssistantTurn(raw=data)
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                turn.text += block.get("text", "")
            elif btype == "thinking":
                turn.thinking.append(ir.ThinkingPart(block.get("thinking", ""),
                                                     block.get("signature")))
            elif btype == "tool_use":
                turn.tool_calls.append(ir.ToolUsePart(
                    id=block.get("id", ""), name=block.get("name", ""),
                    args=block.get("input") or {}))
            elif btype == "server_tool_use":
                # Anthropic-built-in tools (web_search, computer, etc.).  These
                # look like tool_use to the caller, but their results arrive as
                # web_search_tool_result / similar blocks in the same response.
                turn.tool_calls.append(ir.ToolUsePart(
                    id=block.get("id", ""), name=block.get("name", ""),
                    args=block.get("input") or {}))
        sr = data.get("stop_reason", "end_turn")
        turn.stop_reason = {"end_turn": "stop", "stop_sequence": "stop",
                            "max_tokens": "length", "tool_use": "tool_call",
                            "refusal": "content_filter", "pause_turn": "stop",
                            "compaction": "stop"}.get(sr, "stop")
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
        etype = payload.get("type", event)
        out: list[dl.IRStreamDelta] = []
        if etype == "message_start":
            m = payload.get("message", {})
            out.append(dl.StreamStart(model=m.get("model", "")))
            u = m.get("usage") or {}
            self._pending_prompt = u.get("input_tokens", 0)
            self._pending_cached = u.get("cache_read_input_tokens", 0)
            self._pending_cache_creation = u.get("cache_creation_input_tokens", 0)
        elif etype == "content_block_start":
            cb = payload.get("content_block", {})
            idx = payload.get("index", 0)
            if cb.get("type") in ("tool_use", "server_tool_use"):
                self._tool_indices.add(idx)
                out.append(dl.ToolCallOpen(index=idx, id=cb.get("id", ""), name=cb.get("name", "")))
        elif etype == "content_block_delta":
            d = payload.get("delta", {})
            dtype = d.get("type")
            if dtype == "text_delta":
                out.append(dl.TextDelta(d.get("text", "")))
            elif dtype == "thinking_delta":
                out.append(dl.ThinkingDelta(d.get("thinking", "")))
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
            d = payload.get("delta", {})
            u = payload.get("usage") or {}
            sr = d.get("stop_reason", "end_turn")
            out_details = u.get("output_tokens_details") or {}
            out.append(dl.UsageFinal(
                prompt=getattr(self, "_pending_prompt", 0),
                cached=getattr(self, "_pending_cached", 0),
                cache_creation=getattr(self, "_pending_cache_creation", 0),
                reasoning=out_details.get("thinking_tokens", 0),
                output=u.get("output_tokens", 0)))
            out.append(dl.Finish(
                {"end_turn": "stop", "stop_sequence": "stop",
                 "max_tokens": "length", "tool_use": "tool_call",
                 "refusal": "content_filter", "pause_turn": "stop",
                 "compaction": "stop"}.get(sr, "stop"),
                stop_sequence=d.get("stop_sequence")))
        elif etype == "message_stop":
            out.append(dl.StreamEnd())
        elif etype == "error":
            err = payload.get("error", {})
            out.append(dl.StreamError(message=err.get("message", "unknown anthropic error"),
                                      kind="status"))
        return out

