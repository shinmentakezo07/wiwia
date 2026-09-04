"""OpenCode Zen provider adapter (``opencode.ai/zen``).

Zen is a multi-protocol gateway: the same base URL serves four upstream wire
formats, chosen per model (see ``https://opencode.ai/docs/zen`` endpoints
table and the ``models.dev`` ``opencode`` provider entry, whose base
``api`` is ``https://opencode.ai/zen/v1`` with per-model SDK overrides)::

    responses  gpt-*, grok-*, muse-spark-*   POST {base}/responses         (Responses API)
    messages   claude-*, qwen*               POST {base}/messages          (Anthropic Messages API)
    gemini     gemini-*                      POST {base}/models/{id}:...   (Gemini generateContent)
    chat       everything else               POST {base}/chat/completions  (OpenAI Chat API)

The adapter routes by model prefix, delegates chat/messages/gemini to the
existing adapters, and implements a minimal Responses upstream (text +
reasoning + function tools) for the responses family. Auth is a Zen API key
as ``Authorization: Bearer`` plus a live ``User-Agent: opencode/<version>``
— Cloudflare returns ``403 error code: 1010`` without a browser-like UA, so
the version is read live from :mod:`wiwi.providers.opencode_version` (5-min
TTL background refresh, no restart needed).
"""

from __future__ import annotations

import json
from typing import Any, Literal

import orjson
import structlog

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.opencode_version import build_user_agent
from wiwi.streaming import deltas as dl

log = structlog.get_logger("wiwi.opencode_adapter")

OPENCODE_ZEN_BASE = "https://opencode.ai/zen/v1"

Route = Literal["responses", "messages", "gemini", "chat"]

_RESPONSES_PREFIXES = ("gpt-", "grok-", "muse-spark-")
_MESSAGES_PREFIXES = ("claude-", "qwen")
_GEMINI_PREFIXES = ("gemini-",)


def route_for_model(model_id: str) -> Route:
    """Pick the Zen upstream protocol for a native model id."""
    m = (model_id or "").strip().lower()
    if m.startswith(_RESPONSES_PREFIXES):
        return "responses"
    if m.startswith(_MESSAGES_PREFIXES):
        return "messages"
    if m.startswith(_GEMINI_PREFIXES):
        return "gemini"
    return "chat"


def _base(base_url: str) -> str:
    return (base_url or OPENCODE_ZEN_BASE).rstrip("/") or OPENCODE_ZEN_BASE


class OpencodeAdapter:
    """Multi-protocol Zen adapter with live opencode User-Agent headers."""

    provider_type = "opencode"
    force_stream = False

    def __init__(self) -> None:
        self._chat = OpenAIAdapter()
        self._msg = AnthropicAdapter()
        self._gem = GeminiAdapter()
        self._last_route: Route = "chat"
        # Responses-upstream per-stream state (mirrors OpenAIAdapter's).
        self._resp_tools: dict[str, dict[str, Any]] = {}  # item_id -> entry
        self._resp_next_index = 0
        self._resp_started = False
        self._resp_ended = False

    def reset(self) -> None:
        self._chat.reset()
        self._msg.reset()
        self._gem.reset()
        self._last_route = "chat"
        self._resp_tools.clear()
        self._resp_next_index = 0
        self._resp_started = False
        self._resp_ended = False

    # -- auth / URL ------------------------------------------------------
    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key.secret.strip()}",
            "User-Agent": build_user_agent(),
            "HTTP-Referer": "https://opencode.ai/",
            "X-Title": "opencode",
            "anthropic-version": "2023-06-01",
        }

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        route = route_for_model(model_id)
        self._last_route = route
        base = _base(base_url)
        if route == "responses":
            return f"{base}/responses"
        if route == "messages":
            return f"{base}/messages"
        if route == "gemini":
            if stream:
                return f"{base}/models/{model_id}:streamGenerateContent?alt=sse"
            return f"{base}/models/{model_id}:generateContent"
        return f"{base}/chat/completions"

    # -- request encoding --------------------------------------------------
    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        route = route_for_model(model_id)
        self._last_route = route
        if route == "messages":
            return self._msg.encode_request(req, model_id, dict(deployment_params))
        if route == "gemini":
            return self._gem.encode_request(req, model_id, dict(deployment_params))
        if route == "responses":
            return _encode_responses_request(req, model_id, deployment_params)
        params = dict(deployment_params)
        params["provider_type"] = "openai"
        return self._chat.encode_request(req, model_id, params)

    # -- response decoding ---------------------------------------------------
    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        if self._last_route == "responses":
            return _decode_responses_response(body)
        if self._last_route == "messages":
            return self._msg.decode_response(status, body)
        if self._last_route == "gemini":
            return self._gem.decode_response(status, body)
        return self._chat.decode_response(status, body)

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        if data == "[DONE]":
            if self._last_route == "responses":
                if self._resp_ended:
                    return []
                self._resp_ended = True
                return [dl.StreamEnd()]
            return self._sub().decode_stream_event(event, data)
        if self._last_route == "responses" and self._resp_ended:
            return []
        err = _envelope_stream_error(data)
        if err is not None:
            if self._last_route == "responses":
                self._resp_ended = True
            return [err]
        if self._last_route == "responses":
            return self._decode_responses_stream_event(event, data)
        return self._sub().decode_stream_event(event, data)

    def _sub(self) -> Any:
        if self._last_route == "messages":
            return self._msg
        if self._last_route == "gemini":
            return self._gem
        return self._chat

    # -- responses stream decode (stateful) ----------------------------------
    def _decode_responses_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        try:
            payload = orjson.loads(data)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        etype = str(payload.get("type") or event or "")
        out: list[dl.IRStreamDelta] = []
        if not self._resp_started:
            out.append(dl.StreamStart(model=""))
            self._resp_started = True
        if etype == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                out.append(dl.TextDelta(delta))
            return out
        if etype == "response.reasoning_summary_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                out.append(dl.ThinkingDelta(delta))
            return out
        if etype == "response.output_item.added":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if item.get("type") == "function_call":
                item_id = str(item.get("id") or item.get("call_id") or "")
                if item_id and item_id not in self._resp_tools:
                    idx = self._resp_next_index
                    self._resp_next_index += 1
                    self._resp_tools[item_id] = {
                        "index": idx, "name": str(item.get("name") or ""),
                        "call_id": str(item.get("call_id") or item_id),
                    }
                    out.append(dl.ToolCallOpen(index=idx,
                                               id=self._resp_tools[item_id]["call_id"],
                                               name=self._resp_tools[item_id]["name"]))
            return out
        if etype == "response.function_call_arguments.delta":
            item_id = str(payload.get("item_id") or "")
            delta = payload.get("delta")
            entry = self._resp_tools.get(item_id)
            if entry is None or not isinstance(delta, str) or not delta:
                return out
            out.append(dl.ToolCallArgsDelta(index=entry["index"], args_fragment=delta))
            return out
        if etype in ("response.function_call_arguments.done",
                     "response.output_item.done"):
            item = payload.get("item") if isinstance(payload.get("item"), dict) else None
            item_id = str(payload.get("item_id") or (item or {}).get("id") or "")
            if etype == "response.output_item.done" and item is not None:
                if item.get("type") != "function_call":
                    return out
                item_id = str(item.get("id") or item_id)
                entry = self._resp_tools.get(item_id)
                if entry is None:
                    # Done without a prior added (single-shot item): open then close.
                    idx = self._resp_next_index
                    self._resp_next_index += 1
                    args = item.get("arguments") or "{}"
                    entry = {"index": idx, "name": str(item.get("name") or ""),
                             "call_id": str(item.get("call_id") or item_id)}
                    out.append(dl.ToolCallOpen(index=idx, id=entry["call_id"],
                                               name=entry["name"]))
                    if isinstance(args, str) and args and args != "{}":
                        out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=args))
                    out.append(dl.ToolCallClose(index=idx))
                    return out
            entry = self._resp_tools.pop(item_id, None)
            if entry is None:
                return out
            if etype == "response.function_call_arguments.done":
                args = payload.get("arguments")
                if isinstance(args, str) and args and args != "{}":
                    out.append(dl.ToolCallArgsDelta(index=entry["index"],
                                                    args_fragment=args))
            out.append(dl.ToolCallClose(index=entry["index"]))
            return out
        if etype in ("response.completed", "response.incomplete"):
            resp = payload.get("response") if isinstance(payload.get("response"), dict) else {}
            u = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
            in_det = u.get("input_tokens_details") if isinstance(
                u.get("input_tokens_details"), dict) else {}
            out_det = u.get("output_tokens_details") if isinstance(
                u.get("output_tokens_details"), dict) else {}
            out.append(dl.UsageFinal(
                prompt=int(u.get("input_tokens", 0) or 0),
                cached=int(in_det.get("cached_tokens", 0) or 0),
                reasoning=int(out_det.get("reasoning_tokens", 0) or 0),
                output=int(u.get("output_tokens", 0) or 0)))
            for entry in sorted(self._resp_tools.values(), key=lambda e: e["index"]):
                out.append(dl.ToolCallClose(index=entry["index"]))
            self._resp_tools.clear()
            incomplete = etype == "response.incomplete" or resp.get("status") == "incomplete"
            if incomplete:
                out.append(dl.Finish("length"))
            elif self._resp_next_index > 0:
                # A function call opened during this stream: the turn ended
                # with tool calls (mirrors the OpenAI finish_reason mapping).
                out.append(dl.Finish("tool_call"))
            else:
                out.append(dl.Finish("stop"))
            out.append(dl.StreamEnd())
            self._resp_ended = True
            return out
        if etype == "response.failed":
            resp = payload.get("response") if isinstance(payload.get("response"), dict) else {}
            err_obj = resp.get("error") if isinstance(resp.get("error"), dict) else {}
            msg = str(err_obj.get("message") or payload.get("message")
                      or "opencode responses stream failed")
            self._resp_ended = True
            return out + [dl.StreamError(message=msg, kind="status")]
        return out


def _envelope_stream_error(data: str) -> dl.StreamError | None:
    """Surface Zen/Cloudflare error envelopes riding a 200 SSE chunk."""
    try:
        chunk = orjson.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(chunk, dict) or chunk.get("choices"):
        return None
    if isinstance(chunk.get("output"), list):
        return None
    err = chunk.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "opencode stream error")
        return dl.StreamError(message=msg, kind="status")
    if isinstance(err, str) and err.strip():
        # Zen also sends the bare-string shape: {"error": "rate limited"}.
        return dl.StreamError(message=err.strip(), kind="status")
    return None


def _encode_responses_request(req: ir.Request, model_id: str,
                              deployment_params: dict[str, Any]) -> dict[str, Any]:
    """Render IR as an OpenAI Responses API request body for Zen."""
    g = req.gen_params
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role == "system":
            instructions_parts.extend(p.text for p in m.parts
                                      if isinstance(p, ir.TextPart) and p.text)
            continue
        if m.role == "tool":
            for p in m.parts:
                if isinstance(p, ir.ToolResultPart):
                    input_items.append({"type": "function_call_output",
                                        "call_id": p.tool_use_id,
                                        "output": p.content or ""})
            continue
        # user / assistant roles
        text_buf: list[str] = []
        image_parts: list[dict[str, Any]] = []
        for p in m.parts:
            if isinstance(p, ir.TextPart):
                if p.text:
                    text_buf.append(p.text)
            elif isinstance(p, ir.ImagePart):
                url = p.url or (f"data:{p.mime};base64,{p.b64}" if p.b64 else "")
                if url:
                    image_parts.append({"type": "input_image", "image_url": url})
            elif isinstance(p, ir.ToolUsePart):
                input_items.append({"type": "function_call",
                                    "call_id": p.id, "name": p.name,
                                    "arguments": p.raw_args or json.dumps(p.args)})
            elif isinstance(p, ir.ToolResultPart):
                input_items.append({"type": "function_call_output",
                                    "call_id": p.tool_use_id,
                                    "output": p.content or ""})
            elif isinstance(p, ir.ThinkingPart) and p.text:
                input_items.append({"type": "reasoning", "summary": [
                    {"type": "summary_text", "text": p.text}]})
            elif isinstance(p, (ir.AudioPart, ir.DocumentPart)):
                log.warning("dropping_unsupported_part_for_responses",
                            part=type(p).__name__, provider="opencode")
        if text_buf or image_parts:
            content: list[dict[str, Any]] = []
            for t in text_buf:
                ctype = "output_text" if m.role == "assistant" else "input_text"
                content.append({"type": ctype, "text": t})
            content.extend(image_parts)
            input_items.append({"type": "message",
                                "role": "assistant" if m.role == "assistant" else "user",
                                "content": content})
    body: dict[str, Any] = {"model": model_id, "input": input_items,
                            "stream": req.stream}
    if instructions_parts:
        body["instructions"] = "\n".join(instructions_parts)
    mt = g.max_tokens or deployment_params.get("max_tokens")
    if mt:
        body["max_output_tokens"] = mt
    if g.temperature is not None:
        body["temperature"] = g.temperature
    if g.top_p is not None:
        body["top_p"] = g.top_p
    if g.reasoning_effort:
        body["reasoning"] = {"effort": g.reasoning_effort}
    elif g.thinking_budget is not None:
        effort = g.effective_reasoning_effort()
        if effort:
            body["reasoning"] = {"effort": effort}
    if g.response_format and g.response_format.type != "text":
        if g.response_format.type == "json_schema" and g.response_format.json_schema:
            fmt: dict[str, Any] = {"type": "json_schema",
                                   "name": g.response_format.name or "response",
                                   "schema": g.response_format.json_schema}
            if g.response_format.strict is not None:
                fmt["strict"] = g.response_format.strict
            body["text"] = {"format": fmt}
        elif g.response_format.type == "json_object":
            body["text"] = {"format": {"type": "json_object"}}
    if req.tools:
        tools: list[dict[str, Any]] = []
        for t in req.tools:
            if t.builtin is not None:
                if t.builtin == "web_search":
                    tools.append({"type": "web_search"})
                else:
                    log.warning("dropping_unhostable_builtin_tool",
                                builtin=t.builtin, provider="opencode")
                continue
            fn: dict[str, Any] = {"type": "function", "name": t.name,
                                  "description": t.description,
                                  "parameters": t.parameters_json_schema}
            if t.strict is not None:
                fn["strict"] = t.strict
            tools.append(fn)
        if tools:
            body["tools"] = tools
            tc = req.tool_choice
            if isinstance(tc, ir.ToolChoiceNone):
                body["tool_choice"] = "none"
            elif isinstance(tc, ir.ToolChoiceAuto):
                body["tool_choice"] = "auto"
            elif isinstance(tc, ir.ToolChoiceRequired):
                body["tool_choice"] = "required"
            elif isinstance(tc, ir.ToolChoiceNamed):
                body["tool_choice"] = {"type": "function", "name": tc.name}
    if g.parallel_tool_calls is not None:
        body["parallel_tool_calls"] = g.parallel_tool_calls
    for k, v in deployment_params.get("extra_body", {}).items():
        body.setdefault(k, v)
    return body


def _decode_responses_response(body: bytes) -> ir.AssistantTurn:
    data = orjson.loads(body)
    turn = ir.AssistantTurn(raw=data if isinstance(data, dict) else {})
    if not isinstance(data, dict):
        return turn
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") in (
                        "output_text", "text", "refusal"):
                    turn.text += c.get("text") or c.get("refusal") or ""
        elif itype == "reasoning":
            for s in item.get("summary") or []:
                if isinstance(s, dict) and s.get("text"):
                    turn.thinking.append(ir.ThinkingPart(s["text"]))
        elif itype == "function_call":
            raw_args = item.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else {}
            except json.JSONDecodeError:
                args = {}
            turn.tool_calls.append(ir.ToolUsePart(
                id=str(item.get("call_id") or item.get("id") or ""),
                name=str(item.get("name") or ""),
                args=args if isinstance(args, dict) else {},
                raw_args=raw_args if isinstance(raw_args, str) else "{}"))
        # web_search_call and other hosted traces are provider-executed:
        # their text (if any) already arrived as message items — never emit
        # a phantom function call the client would try to execute.
    status = data.get("status")
    if status == "incomplete":
        turn.stop_reason = "length"
    elif status == "failed":
        turn.stop_reason = "stop"
    elif turn.tool_calls:
        turn.stop_reason = "tool_call"
    else:
        turn.stop_reason = "stop"
    u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    in_det = u.get("input_tokens_details") if isinstance(
        u.get("input_tokens_details"), dict) else {}
    out_det = u.get("output_tokens_details") if isinstance(
        u.get("output_tokens_details"), dict) else {}
    turn.usage = ir.Usage(
        prompt_tokens=int(u.get("input_tokens", 0) or 0),
        completion_tokens=int(u.get("output_tokens", 0) or 0),
        cached_tokens=int(in_det.get("cached_tokens", 0) or 0),
        reasoning_tokens=int(out_det.get("reasoning_tokens", 0) or 0),
    )
    return turn
