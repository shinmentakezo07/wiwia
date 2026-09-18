"""OpenCode Zen provider adapter (``opencode.ai/zen``).

Zen is a multi-protocol gateway: the same base URL serves four upstream wire
formats, chosen per model (see ``https://opencode.ai/docs/zen`` endpoints
table and the ``models.dev`` ``opencode`` provider entry, whose base
``api`` is ``https://opencode.ai/zen/v1`` with per-model SDK overrides)::

    responses  gpt-*, grok-*, muse-spark-*   POST {base}/responses         (Responses API)
    messages   claude-*, qwen*, union-alpha  POST {base}/messages          (Anthropic Messages API)
    gemini     gemini-*                      POST {base}/models/{id}:...   (Gemini generateContent)
    chat       everything else               POST {base}/chat/completions  (OpenAI Chat API)

The adapter routes by model prefix, delegates chat/messages/gemini to the
existing adapters, and implements a minimal Responses upstream (text +
reasoning + function tools) for the responses family.

Auth follows the **wire**, not the adapter: each of Zen's four front ends reads
its own credential scheme, and a credential sent in the wrong one is simply not
read — Zen answers ``401 AuthError "Missing API key."`` ("Missing", not
"Invalid": the credential never arrived)::

    chat / responses   Authorization: Bearer <key>   (OpenAI wire)
    messages           x-api-key: <key>              (Anthropic wire)
    gemini             x-goog-api-key: <key>         (Gemini wire)

All three verified live 2026-09-17 against a real ``sk-…`` Zen key: the correct
scheme on each route advances the request to the *next* gate
(``401 CreditsError "No payment method"``), while every wrong scheme — and the
no-credential case — return the identical ``AuthError``. Per-route probe
matrices live in ``tests/test_fix_round72.py`` (messages) and
``tests/test_fix_round73.py`` (gemini). The Gemini route does **not** use the
querystring: ``?key=`` probes identically to sending no credential at all, and
``wiwi/core/recovery.py:build_url`` appends a querystring key only when
``provider_type == "gemini"`` — this adapter's type is ``"opencode"``, so
nothing was ever appended there.

Every request also carries a live
``User-Agent: opencode/<version>`` — Cloudflare returns ``403 error code:
1010`` without a browser-like UA, so the version is read live from
:mod:`wiwi.providers.opencode_version` (5-min TTL background refresh, no
restart needed).

Every request also carries the official client's metadata headers
(``packages/opencode/src/session/llm/request.ts``):
``x-opencode-session`` + ``x-opencode-request`` (per-request ids, stable
across header rebuilds within one request), ``x-opencode-client: cli``, and
``x-opencode-project: global`` (the CLI's fallback when no workspace is
bound). The edge reads them for metrics/sticky routing on every model, so
they ride all traffic. Live probes 2026-09-17: a request with a bearer
(whether real or placeholder) clears the session gate and reaches the auth
gate (``401 AuthError`` for a bad key), proving the spoof still passes; a
keyless request to any ``*-free`` model is rejected with ``403 FreeTierError``
("OpenCode's free tier can only be used from within OpenCode") on all three
free routes (chat, responses, messages). Keyless anonymous free-tier access
(which returned 200 until 2026-09-16) is therefore retired upstream — free
models now require a valid ``OPENCODE_API_KEY``. ``anthropic-version`` rides
the messages route only; ``HTTP-Referer``/``X-Title`` are referral
attribution, not part of the CLI fingerprint.
"""

from __future__ import annotations

import json
import uuid
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
_MESSAGES_PREFIXES = ("claude-", "qwen", "union-alpha")
_GEMINI_PREFIXES = ("gemini-",)

# Config keys are validated non-empty (KeyDef._key_required), so a keyless
# free-tier setup declares itself with this literal sentinel and the
# adapter omits Authorization entirely for it.
ANONYMOUS_KEY_SENTINEL = "anonymous"

# Each Zen front end reads its own credential header, and a credential sent in
# any other one is invisible to it (see the module docstring for the live probe
# matrix). The value here is the *header name*; "Authorization" additionally
# needs the "Bearer " prefix, applied at the write site.
_CREDENTIAL_HEADER: dict[Route, str] = {
    "messages": "x-api-key",
    "gemini": "x-goog-api-key",
    "chat": "Authorization",
    "responses": "Authorization",
}


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
        # Official-client fingerprint ids: generated lazily so they stay
        # stable across the 401-refresh retry path's header rebuilds (same
        # adapter instance) while every request gets its own fresh pair
        # (fresh_adapter on the hot path).
        self._spoof_session: str | None = None
        self._spoof_request: str | None = None
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
        self._spoof_session = None
        self._spoof_request = None
        self._resp_tools.clear()
        self._resp_next_index = 0
        self._resp_started = False
        self._resp_ended = False

    # -- auth / URL ------------------------------------------------------
    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        h: dict[str, str] = {
            "User-Agent": build_user_agent(),
            "HTTP-Referer": "https://opencode.ai/",
            "X-Title": "opencode",
        }
        # Anthropic's API version names its own Messages endpoint: it rides
        # the messages route only (genuine Anthropic wire via _msg, or Zen's
        # /messages). The Responses/Chat/Gemini endpoints never need it.
        if self._last_route == "messages":
            h["anthropic-version"] = "2023-06-01"
        # Anonymous sentinel: omits every credential (a placeholder bearer
        # would be 401 Invalid API key). NOTE (2026-09-17): Zen retired
        # keyless free-tier access — anonymous requests to *-free models now
        # get 403 FreeTierError on every route, so free models need a valid
        # OPENCODE_API_KEY. The sentinel stays for setups that probe paid
        # models' auth gate without a key on file.
        if key.secret.strip().lower() != ANONYMOUS_KEY_SENTINEL:
            # Scheme follows the route; see _CREDENTIAL_HEADER and the module
            # docstring. `_last_route` is written by build_url()/encode_request()
            # ahead of headers() on every hot path; a cold call defaults to
            # "chat" (the OpenAI-wire scheme). The debug line names route and
            # scheme — a wrong scheme is silently unread upstream, so this is
            # what turns that into a one-line diagnosis. Never logs the key.
            scheme = _CREDENTIAL_HEADER.get(self._last_route, "Authorization")
            value = key.secret.strip()
            h[scheme] = value if scheme != "Authorization" else f"Bearer {value}"
            log.debug("opencode_credential_scheme", route=self._last_route,
                      scheme=scheme, label=key.label)
        else:
            log.debug("opencode_credential_omitted", route=self._last_route,
                      reason="anonymous_sentinel", label=key.label)
        # Official-client fingerprint, on every model, mirroring the
        # opencode CLI (packages/opencode/src/session/llm/request.ts):
        # per-request session + request ids, client tag, and project id
        # (the CLI's global fallback when no workspace is bound). The edge
        # reads them for metrics/sticky routing; ids stay stable per adapter
        # instance across the 401-refresh retry path's header rebuilds and
        # rotate on reset()/fresh instance.
        if self._spoof_session is None:
            self._spoof_session = f"ses_{uuid.uuid4().hex[:24]}"
        if self._spoof_request is None:
            self._spoof_request = f"msg_{uuid.uuid4().hex[:24]}"
        h["x-opencode-session"] = self._spoof_session
        h["x-opencode-request"] = self._spoof_request
        h["x-opencode-client"] = "cli"
        h["x-opencode-project"] = "global"
        return h

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
                # A stream that ends on the sentinel instead of
                # ``response.completed`` still delivered function_call items;
                # returning a bare StreamEnd left them unterminated and the
                # gateway synthesized Finish("stop") for a tool-call turn
                # (AUDIT #133 class). ``_resp_ended`` makes every later event a
                # no-op, so those entries could never be drained afterwards.
                # Mirrors the completed/incomplete branch below.
                out: list[dl.IRStreamDelta] = []
                for entry in sorted(self._resp_tools.values(),
                                    key=lambda e: e["index"]):
                    if not entry.get("closed"):
                        out.append(dl.ToolCallClose(index=entry["index"]))
                self._resp_tools.clear()
                had_calls = self._resp_next_index > 0
                self._resp_next_index = 0
                if had_calls:
                    # A function call opened during this stream: the turn ended
                    # with tool calls, so the stop reason is content-derived.
                    out.append(dl.Finish("tool_call"))
                out.append(dl.StreamEnd())
                return out
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
                        "buf": "",  # accumulated .delta fragments
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
            entry["buf"] += delta
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
                    out.append(dl.ToolCallOpen(index=idx,
                                               id=str(item.get("call_id") or item_id),
                                               name=str(item.get("name") or "")))
                    if isinstance(args, str) and args and args != "{}":
                        out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=args))
                    out.append(dl.ToolCallClose(index=idx))
                    return out
                if entry.get("closed"):
                    # args.done already closed this item (the normal order is
                    # added → deltas → args.done → item.done): the item is
                    # fully delivered; emitting anything again would reopen a
                    # duplicate tool call.
                    return out
                entry["closed"] = True
                out.append(dl.ToolCallClose(index=entry["index"]))
                return out
            entry = self._resp_tools.get(item_id)
            if entry is None or entry.get("closed"):
                return out
            entry["closed"] = True
            if etype == "response.function_call_arguments.done":
                # ``arguments`` on .done is CUMULATIVE — the complete final
                # string, not a fragment (verified live 2026-09-07). The
                # incremental .delta events already delivered it; re-emitting
                # the full string as another fragment duplicates the payload
                # and the client's concatenated arguments stop parsing as
                # JSON. Only emit when it adds information: the no-fragments
                # single-shot case, or a suffix repairing a truncated delta
                # stream.
                args = payload.get("arguments")
                if isinstance(args, str) and args and args != "{}":
                    buf = entry.get("buf") or ""
                    if not buf:
                        out.append(dl.ToolCallArgsDelta(index=entry["index"],
                                                        args_fragment=args))
                    elif args.startswith(buf) and len(args) > len(buf):
                        out.append(dl.ToolCallArgsDelta(
                            index=entry["index"], args_fragment=args[len(buf):]))
                    # buf == args: fragments already complete — emit nothing.
                    # buf not a prefix: trust the streamed fragments.
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
                if not entry.get("closed"):
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
            # Clear per-stream tool state before short-circuiting: `_resp_ended`
            # makes every later event a no-op, so stale entries could never be
            # drained and would leak into the next use of a shared adapter
            # (AUDIT #77). Mirrors the completed/incomplete branch above.
            self._resp_tools.clear()
            self._resp_next_index = 0
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
            tool_text: list[str] = []
            for p in m.parts:
                if isinstance(p, ir.ToolResultPart):
                    if p.block_type != "tool_result":
                        # Provider-executed tool result: no function_call exists
                        # for it, so a function_call_output would be an orphan.
                        tool_text.append(p.content or "")
                        continue
                    input_items.append({"type": "function_call_output",
                                        "call_id": p.tool_use_id,
                                        "output": p.content or ""})
            if tool_text:
                input_items.append({"type": "message", "role": "user",
                                    "content": [{"type": "input_text", "text": t}
                                                for t in tool_text if t]})
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
                if p.builtin is not None:
                    # Provider-hosted call: the backend cannot host it and the
                    # client cannot dispatch it, so a function_call here is a
                    # phantom with no output. Its result rides as text.
                    continue
                input_items.append({"type": "function_call",
                                    "call_id": p.id, "name": p.name,
                                    "arguments": p.raw_args or json.dumps(p.args)})
            elif isinstance(p, ir.ToolResultPart):
                if p.block_type != "tool_result":
                    # Provider-executed result: fold the payload into the
                    # message text rather than emitting an orphan output.
                    if p.content:
                        text_buf.append(p.content)
                    continue
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
    # Reconcile all three effort spellings through the shared resolver so an
    # Anthropic output_config.effort reaches this route too (AUDIT #156).
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
            # The Responses dialect is the one non-Anthropic surface that hosts
            # tool search, so a deferral flag rides through natively rather
            # than being flattened.
            if t.defer_loading is not None:
                fn["defer_loading"] = t.defer_loading
            # No Responses field for Anthropic's examples: render them into the
            # description so a tool that arrived with worked examples is not
            # indistinguishable from one without.
            if t.input_examples:
                rendered = json.dumps(t.input_examples, ensure_ascii=False)
                fn["description"] = (
                    f"{t.description}\n\nExample inputs:\n{rendered}"
                    if t.description else f"Example inputs:\n{rendered}")
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
    # The Responses dialect spells it ``parallel_tool_calls``; an Anthropic
    # client sets ``disable_parallel_tool_use`` instead, which the IR keeps
    # separate. Reading only the former meant a Claude Code request that
    # explicitly serialized its tool calls got concurrent ones anyway
    # (AUDIT #156).
    parallel = g.parallel_tool_calls
    if parallel is None and g.disable_parallel_tool_use is not None:
        parallel = not g.disable_parallel_tool_use
    if parallel is not None:
        body["parallel_tool_calls"] = parallel
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
