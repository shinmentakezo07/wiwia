"""Anthropic adapter: Messages API encode/decode incl. SSE event folding."""

from __future__ import annotations

import json
from typing import Any

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.streaming import deltas as dl

DEFAULT_MAX_TOKENS = 4096


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


class AnthropicAdapter:
    provider_type = "anthropic"

    def __init__(self) -> None:
        self._tool_indices: set[int] = set()

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        return {"x-api-key": key.secret, "anthropic-version": "2023-06-01"}

    def build_url(self, base_url: str, model_id: str, stream: bool, kind: str) -> str:
        base = base_url.rstrip("/")
        if kind == "count_tokens":
            return f"{base}/messages/count_tokens"
        return f"{base}/messages" + ("?stream=true" if False else "")

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
                    b: dict[str, Any] = {"type": "text", "text": p.text}
                    if p.cache_control:
                        b["cache_control"] = p.cache_control
                    blocks.append(b)
                elif isinstance(p, ir.ImagePart):
                    src = ({"type": "url", "url": p.url} if p.url
                           else {"type": "base64", "media_type": p.mime, "data": p.b64})
                    blocks.append({"type": "image", "source": src})
                elif isinstance(p, ir.ToolUsePart):
                    blocks.append({"type": "tool_use", "id": p.id, "name": p.name,
                                   "input": p.args})
                elif isinstance(p, ir.ToolResultPart):
                    blocks.append({"type": "tool_result", "tool_use_id": p.tool_use_id,
                                   "content": p.content})
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
        if system:
            body["system"] = system
        if g.temperature is not None:
            body["temperature"] = g.temperature
        if g.top_p is not None:
            body["top_p"] = g.top_p
        if g.stop:
            body["stop_sequences"] = g.stop
        if g.thinking_budget:
            body["thinking"] = {"type": "enabled", "budget_tokens": g.thinking_budget}
        if req.tools:
            body["tools"] = [
                {"name": t.name, "description": t.description,
                 "input_schema": t.parameters_json_schema}
                for t in req.tools
            ]
            tc = req.tool_choice
            if isinstance(tc, ir.ToolChoiceNone):
                body["tool_choice"] = {"type": "auto"}  # closest; none unsupported pre-4.x
            elif isinstance(tc, ir.ToolChoiceRequired):
                body["tool_choice"] = {"type": "any"}
            elif isinstance(tc, ir.ToolChoiceNamed):
                body["tool_choice"] = {"type": "tool", "name": tc.name}
        if req.stream:
            body["stream"] = True
        return body

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = json.loads(body)
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
        sr = data.get("stop_reason", "end_turn")
        turn.stop_reason = {"end_turn": "stop", "stop_sequence": "stop",
                            "max_tokens": "length", "tool_use": "tool_call",
                            "refusal": "content_filter"}.get(sr, "stop")
        u = data.get("usage") or {}
        turn.usage = ir.Usage(
            prompt_tokens=u.get("input_tokens", 0),
            completion_tokens=u.get("output_tokens", 0),
            cached_tokens=u.get("cache_read_input_tokens", 0),
            cache_creation_tokens=u.get("cache_creation_input_tokens", 0),
        )
        return turn

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        try:
            payload = json.loads(data)
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
            if cb.get("type") == "tool_use":
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
            out.append(dl.UsageFinal(
                prompt=getattr(self, "_pending_prompt", 0),
                cached=getattr(self, "_pending_cached", 0),
                cache_creation=getattr(self, "_pending_cache_creation", 0),
                output=u.get("output_tokens", 0)))
            out.append(dl.Finish({"end_turn": "stop", "stop_sequence": "stop",
                                  "max_tokens": "length", "tool_use": "tool_call",
                                  "refusal": "content_filter"}.get(sr, "stop")))
        elif etype == "message_stop":
            out.append(dl.StreamEnd())
        elif etype == "error":
            err = payload.get("error", {})
            out.append(dl.StreamError(message=err.get("message", "unknown anthropic error"),
                                      kind="status"))
        return out

    def is_done(self, event: str, data: str) -> bool:
        return '"type":"message_stop"' in data.replace(" ", "") or '"type": "message_stop"' in data
