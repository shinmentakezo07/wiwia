"""OpenAI adapter: chat completions API (also serves openai-compatible endpoints)."""

from __future__ import annotations

import json
from typing import Any

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
                    out.append({"role": "tool", "tool_call_id": p.tool_use_id,
                                "content": p.content})
            continue
        content: Any = None
        tool_calls = []
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
        msg: dict[str, Any] = {"role": m.role, "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
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
        if req.stream and req.stream_options_include_usage:
            body["stream_options"] = {"include_usage": True}
        for k, v in deployment_params.get("extra_body", {}).items():
            body.setdefault(k, v)
        return body

    # -- response decoding -----------------------------------------------------
    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = json.loads(body)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        turn = ir.AssistantTurn(text=message.get("content") or "", raw=data)
        for tc in message.get("tool_calls") or []:
            raw_args = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
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

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        if data == "[DONE]":
            return [dl.StreamEnd()]
        try:
            chunk = json.loads(data)
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
            if tc.get("id"):
                # a new tool call opening on the same index closes the previous one
                if getattr(self, "_open_tool_idx", None) == idx:
                    out.append(dl.ToolCallClose(index=idx))
                self._open_tool_idx = idx
                out.append(dl.ToolCallOpen(index=idx, id=tc["id"], name=fn.get("name", "")))
            if fn.get("arguments"):
                out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=fn["arguments"]))
        fr = c.get("finish_reason")
        if fr:
            # close any still-open tool call before finishing
            open_idx = getattr(self, "_open_tool_idx", None)
            if open_idx is not None:
                out.append(dl.ToolCallClose(index=open_idx))
                self._open_tool_idx = None
            out.append(dl.Finish({"stop": "stop", "length": "length",
                                  "tool_calls": "tool_call",
                                  "content_filter": "content_filter"}.get(fr, "stop")))
        return out

