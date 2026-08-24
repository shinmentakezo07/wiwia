"""OpenAI adapter: chat completions API (also serves openai-compatible endpoints)."""

from __future__ import annotations

import json
from typing import Any

import orjson

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.streaming import deltas as dl


def _role_parts_to_content(messages: list[ir.Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            text = " ".join(p.text for p in m.parts if isinstance(p, ir.TextPart))
            out.append({"role": "system", "content": text})
            continue
        if m.role == "tool":
            for p in m.parts:
                if isinstance(p, ir.ToolResultPart):
                    content = f"[tool error] {p.content}" if p.is_error else p.content
                    out.append({"role": "tool", "tool_call_id": p.tool_use_id,
                                "content": content})
            continue
        # user and assistant roles
        content: Any = None
        tool_calls = []
        reasoning = ""
        emitted_tool_results = False
        for p in m.parts:
            if isinstance(p, ir.TextPart):
                if content is None:
                    content = p.text
                elif isinstance(content, str):
                    content = content + p.text
                else:
                    content.append({"type": "text", "text": p.text})
            elif isinstance(p, ir.ImagePart):
                if content is None or isinstance(content, str):
                    content = ([{"type": "text", "text": content}] if content else [])
                url = p.url or f"data:{p.mime};base64,{p.b64}"
                content.append({"type": "image_url", "image_url": {"url": url}})
            elif isinstance(p, ir.ToolUsePart):
                tool_calls.append({
                    "id": p.id, "type": "function",
                    "function": {"name": p.name,
                                 "arguments": p.raw_args or json.dumps(p.args)},
                })
            elif isinstance(p, ir.ThinkingPart):
                reasoning += p.text
            elif isinstance(p, ir.ToolResultPart) and m.role == "user":
                # Anthropic convention: tool results arrive as user-role
                # messages with tool_result content blocks. OpenAI expects
                # them as role=tool messages, so emit one per result.
                tool_content = f"[tool error] {p.content}" if p.is_error else p.content
                out.append({"role": "tool", "tool_call_id": p.tool_use_id,
                            "content": tool_content})
                emitted_tool_results = True
        # If the message was fully consumed as tool_result messages, skip
        # the empty trailing user message (OpenRouter rejects content: null
        # and an empty user message is meaningless).
        if emitted_tool_results and content is None and not tool_calls:
            continue
        # Build the assistant/user message; never send content: null.
        msg: dict[str, Any] = {"role": m.role}
        if content is not None:
            msg["content"] = content
        elif not tool_calls:
            # No content and no tool calls: emit empty string rather than null
            # (OpenRouter and other APIs reject content: null).
            msg["content"] = ""
        if tool_calls:
            msg["tool_calls"] = tool_calls
            if "content" not in msg:
                msg["content"] = None  # OpenAI allows null with tool_calls
        if reasoning and m.role == "assistant":
            msg["reasoning"] = reasoning
            # Also surface as reasoning_content for OpenAI Chat clients whose
            # downstream providers expect that field name (OpenAI itself, plus
            # OpenAI-compatible APIs that follow the OpenAI Chat shape rather
            # than the older OpenAI-compatible "reasoning" convention).
            msg["reasoning_content"] = reasoning
        out.append(msg)
    return out


class OpenAIAdapter:
    provider_type = "openai"

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        h = {"Authorization": f"Bearer {key.secret}"}
        return h

    def build_url(self, base_url: str, model_id: str, stream: bool, kind: str) -> str:
        base = base_url.rstrip("/")
        if kind == "embeddings":
            return f"{base}/embeddings"
        return f"{base}/chat/completions"

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        g = req.gen_params
        body: dict[str, Any] = {
            "model": model_id,
            "messages": _role_parts_to_content(req.messages),
            "stream": req.stream,
        }
        mt = g.max_tokens or deployment_params.get("max_tokens")
        if mt:
            body["max_tokens"] = mt
        if g.temperature is not None:
            body["temperature"] = g.temperature
        if g.top_p is not None:
            body["top_p"] = g.top_p
        if g.stop:
            body["stop"] = g.stop
        if g.seed is not None:
            body["seed"] = g.seed
        if g.parallel_tool_calls is not None:
            body["parallel_tool_calls"] = g.parallel_tool_calls
        # reasoning_effort is OpenAI-specific (o-series / GPT-5.x reasoning
        # models).  openai-compatible backends (OpenRouter, Together, vLLM…)
        # and GMI Cloud often reject the field with a 400, so only forward it
        # when talking to a native OpenAI endpoint (or when the provider type
        # is unknown, which preserves the default behaviour for direct-OpenAI
        # tests).
        ptype = deployment_params.get("provider_type")
        is_native_openai = ptype not in {"openai-compatible", "gmicloud"}
        if g.reasoning_effort:
            if is_native_openai:
                body["reasoning_effort"] = g.reasoning_effort
        elif g.thinking_budget is not None:
            # Client sent thinking_budget (Anthropic dialect) — map to OpenAI reasoning_effort
            effort = g.effective_reasoning_effort()
            if effort and is_native_openai:
                body["reasoning_effort"] = effort
        if g.response_format and g.response_format.type != "text":
            rf: dict[str, Any] = {"type": g.response_format.type}
            if g.response_format.json_schema:
                rf["json_schema"] = {
                    "name": g.response_format.name or "response",
                    "schema": g.response_format.json_schema,
                }
                if g.response_format.strict is not None:
                    rf["json_schema"]["strict"] = g.response_format.strict
            body["response_format"] = rf
        if req.tools:
            body["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.parameters_json_schema}}
                for t in req.tools
            ]
            # Forward strict mode (OpenAI structured outputs / Anthropic strict tool use).
            for i, t in enumerate(req.tools):
                if t.strict is not None:
                    body["tools"][i]["function"]["strict"] = t.strict
            if req.tool_choice is not None:
                tc = req.tool_choice
                if isinstance(tc, ir.ToolChoiceNone):
                    body["tool_choice"] = "none"
                elif isinstance(tc, ir.ToolChoiceAuto):
                    body["tool_choice"] = "auto"
                elif isinstance(tc, ir.ToolChoiceRequired):
                    body["tool_choice"] = "required"
                elif isinstance(tc, ir.ToolChoiceNamed):
                    body["tool_choice"] = {"type": "function", "function": {"name": tc.name}}
            # disable_parallel_tool_use (from Anthropic dialect) maps to
            # parallel_tool_calls=false on the OpenAI side.
            if g.disable_parallel_tool_use is not None:
                body["parallel_tool_calls"] = not g.disable_parallel_tool_use
        if req.stream and req.stream_options_include_usage:
            body["stream_options"] = {"include_usage": True}
        for k, v in deployment_params.get("extra_body", {}).items():
            body.setdefault(k, v)
        # Standard chat params clients send that the IR doesn't model; forward
        # to OpenAI-shaped upstreams. drop_params=False forwards the rest raw.
        _STANDARD = {"frequency_penalty", "presence_penalty", "logprobs",
                     "top_logprobs", "user"}
        for k, v in req.extras.items():
            if k in _STANDARD or not deployment_params.get("drop_params", True):
                body.setdefault(k, v)
        return body

    # -- response decoding -----------------------------------------------------
    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = orjson.loads(body)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        turn = ir.AssistantTurn(text=message.get("content") or "", raw=data)
        # Reasoning models may return reasoning_content in the message body
        # (DeepSeek, OpenRouter, and other OpenAI-compatible providers).
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            turn.thinking.append(ir.ThinkingPart(reasoning))
        for tc in message.get("tool_calls") or []:
            raw_args = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                # Auto-repair truncated JSON instead of dropping to {}.
                from wiwi.streaming.partial_json import _repair_truncated_json
                try:
                    args = json.loads(_repair_truncated_json(raw_args))
                except json.JSONDecodeError:
                    args = {}
            turn.tool_calls.append(ir.ToolUsePart(
                id=tc.get("id", ""), name=tc.get("function", {}).get("name", ""),
                args=args, raw_args=raw_args))
        fr = choice.get("finish_reason", "stop")
        turn.stop_reason = {"stop": "stop", "length": "length", "tool_calls": "tool_call",
                            "content_filter": "content_filter"}.get(fr, "stop")
        u = data.get("usage") or {}
        details_p = (u.get("prompt_tokens_details") or {})
        details_c = (u.get("completion_tokens_details") or {})
        turn.usage = ir.Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            cached_tokens=details_p.get("cached_tokens", 0),
            reasoning_tokens=details_c.get("reasoning_tokens", 0),
        )
        return turn

    def __init__(self) -> None:
        self._open_tool_indices: set[int] = set()
        self._tool_names: dict[int, str] = {}  # accumulated name fragments per index

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        if data == "[DONE]":
            return [dl.StreamEnd()]
        try:
            chunk = orjson.loads(data)
        except json.JSONDecodeError:
            return []
        out: list[dl.IRStreamDelta] = []
        choices = chunk.get("choices") or []
        # usage may ride in ANY chunk — OpenAI/OpenRouter put it in the same
        # final chunk as choices+finish_reason. Parse it whenever present;
        # later cumulative values replace earlier ones.
        u = chunk.get("usage")
        if u:
            dp = u.get("prompt_tokens_details") or {}
            dc = u.get("completion_tokens_details") or {}
            out.append(dl.UsageFinal(
                prompt=u.get("prompt_tokens", 0), cached=dp.get("cached_tokens", 0),
                reasoning=dc.get("reasoning_tokens", 0),
                output=u.get("completion_tokens", 0)))
        if not choices:
            return out
        c = choices[0]
        delta = c.get("delta") or {}
        if delta.get("content"):
            out.append(dl.TextDelta(delta["content"]))
        if delta.get("reasoning_content"):
            out.append(dl.ThinkingDelta(delta["reasoning_content"]))
        tool_calls = delta.get("tool_calls") or []
        for i, tc in enumerate(tool_calls):
            idx = tc.get("index", i)
            fn = tc.get("function") or {}
            name_fragment = fn.get("name", "")
            if tc.get("id"):
                # a new tool call opening on the same index closes the previous one
                if idx in self._open_tool_indices:
                    out.append(dl.ToolCallClose(index=idx))
                self._open_tool_indices.add(idx)
                # Accumulate name: some providers send the full name on the
                # first chunk, others fragment it across subsequent deltas.
                self._tool_names[idx] = name_fragment or ""
                out.append(dl.ToolCallOpen(index=idx, id=tc["id"],
                                           name=self._tool_names[idx]))
            elif name_fragment and idx in self._open_tool_indices:
                # Name fragment on a subsequent delta (no id): accumulate.
                self._tool_names[idx] = self._tool_names.get(idx, "") + name_fragment
            if fn.get("arguments"):
                out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=fn["arguments"]))
        fr = c.get("finish_reason")
        if fr:
            # close ALL still-open tool calls before finishing (parallel tools)
            for open_idx in sorted(self._open_tool_indices):
                out.append(dl.ToolCallClose(index=open_idx))
            self._open_tool_indices.clear()
            self._tool_names.clear()
            out.append(dl.Finish({"stop": "stop", "length": "length",
                                  "tool_calls": "tool_call",
                                  "content_filter": "content_filter"}.get(fr, "stop")))
        return out

