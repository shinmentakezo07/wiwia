"""OpenRouter adapter: OpenAI-compatible chat completions with OpenRouter-specific
parameter translation.

OpenRouter is OpenAI-compatible at the wire level but differs in:
- ``reasoning`` parameter (not ``reasoning_effort``) — a unified object that
  accepts ``effort`` (OpenAI-style) or ``max_tokens`` (Anthropic-style).
- ``reasoning_details`` array in responses (not just ``reasoning_content``).
- Mid-stream errors with ``finish_reason: "error"`` and a top-level ``error``.
- ``: OPENROUTER PROCESSING`` SSE comments (already handled by LineSSEParser).
- ``max_tokens`` is deprecated; ``max_completion_tokens`` is preferred.
- ``usage`` in the final stream chunk when ``stream_options.include_usage`` is set.

See:
  https://openrouter.ai/docs/api_reference/parameters
  https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
  https://openrouter.ai/docs/api/reference/streaming
"""

from __future__ import annotations

import json
from typing import Any

import orjson

from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl


class OpenRouterAdapter(OpenAIAdapter):
    """OpenRouter: extends OpenAI adapter with OpenRouter-specific translations.

    Key translations:
    - ``reasoning_effort`` / ``thinking_budget``  →  ``reasoning: {effort|max_tokens}``
    - ``reasoning_details`` array  →  IR ``ThinkingPart`` (text/summary/encrypted)
    - mid-stream ``error`` + ``finish_reason:"error"``  →  ``StreamError``
    - ``max_tokens``  →  ``max_completion_tokens`` (deprecated → preferred)
    """

    provider_type = "openrouter"

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        return {"Authorization": f"Bearer {key.secret}"}

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        base = base_url.rstrip("/")
        return f"{base}/chat/completions"

    # -- encode: translate IR reasoning config to OpenRouter's ``reasoning`` param -

    def _encode_tools(self, req: ir.Request) -> list[dict[str, Any]] | None:
        """Render IR tools as OpenRouter ``tools`` entries (or None).

        Unlike the OpenAI base, OpenRouter can host builtins: a web_search
        builtin rides the tools array as ``{"type": "openrouter:web_search",
        "parameters": {...}}``. OpenRouter spells the domain blocklist
        ``excluded_domains`` (Anthropic/Responses say ``blocked_domains``).
        """
        if not req.tools:
            return None
        out: list[dict[str, Any]] = []
        for t in req.tools:
            wt = bt.wire_type_for("openrouter", t.builtin) if t.builtin else None
            if t.builtin is not None and wt is None:
                # Unhostable builtin (e.g. code_execution): drop, don't mangle.
                continue
            if t.builtin is not None:
                cfg = t.builtin_config or {}
                params: dict[str, Any] = {}
                for k in ("max_uses", "allowed_domains", "user_location",
                          "search_context_size"):
                    if k in cfg:
                        params[k] = cfg[k]
                if "blocked_domains" in cfg:
                    params["excluded_domains"] = cfg["blocked_domains"]
                out.append({"type": wt, "parameters": params})
                continue
            fn: dict[str, Any] = {"name": t.name, "description": t.description,
                                  "parameters": t.parameters_json_schema}
            if t.strict is not None:
                fn["strict"] = t.strict
            out.append({"type": "function", "function": fn})
        return out or None

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        # Delegate the bulk of the encoding to the OpenAI adapter, then strip
        # reasoning_effort (if present) and replace it with OpenRouter's
        # unified ``reasoning`` parameter.
        body = super().encode_request(req, model_id, deployment_params)

        # Remove OpenAI-native reasoning_effort; OpenRouter uses ``reasoning``
        body.pop("reasoning_effort", None)

        # Strip any per-message reasoning / reasoning_content from history
        # assistant messages. OpenRouter uses ``reasoning_details`` on the
        # response side and does not accept these on input. The OpenAI base
        # already omits them for provider_type=="openrouter" (since this
        # commit), but strip defensively in case a future change re-introduces
        # them or the deployment_params are missing the type.
        for m in body.get("messages", []):
            if isinstance(m, dict) and m.get("role") == "assistant":
                m.pop("reasoning", None)
                m.pop("reasoning_content", None)

        # Rename deprecated max_tokens to max_completion_tokens (OpenRouter
        # docs mark max_tokens as deprecated; some models enforce a minimum
        # of 16 on max_tokens but not max_completion_tokens).
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")

        g = req.gen_params
        reasoning_obj: dict[str, Any] | None = None

        if g.reasoning_effort == "none":
            # Explicitly disable reasoning
            reasoning_obj = {"enabled": False}
        elif g.reasoning_effort:
            # OpenAI-style effort string -> OpenRouter reasoning.effort.
            # OpenRouter accepts: max, xhigh, high, medium, low, minimal, none.
            reasoning_obj = {"effort": g.reasoning_effort}
        elif g.thinking_budget is not None:
            # Anthropic-style token budget -> OpenRouter reasoning.max_tokens.
            # OpenRouter enforces a minimum of 1024 for Anthropic models.
            budget = max(g.thinking_budget, 1024)
            reasoning_obj = {"max_tokens": budget}

        if reasoning_obj is not None:
            body["reasoning"] = reasoning_obj

        # OpenRouter supports stream_options.include_usage; keep it only if
        # the client explicitly requested it (the OpenAI adapter already guards
        # this, but we double-check here for safety).
        if body.get("stream") and not req.stream_options_include_usage:
            body.pop("stream_options", None)

        return body

    # -- decode: extract reasoning_details and mid-stream errors -----------------

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = orjson.loads(body)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        turn = ir.AssistantTurn(text=message.get("content") or "", raw=data)

        # OpenRouter returns reasoning in ``reasoning`` (string) or
        # ``reasoning_details`` (array of structured objects).  The string
        # form is the common case; the array form carries encrypted/summary
        # blocks that we flatten into ThinkingPart.
        reasoning_str = message.get("reasoning") or message.get("reasoning_content")
        if reasoning_str:
            turn.thinking.append(ir.ThinkingPart(reasoning_str))

        for rd in message.get("reasoning_details") or []:
            rtype = rd.get("type", "")
            if rtype == "reasoning.text":
                turn.thinking.append(ir.ThinkingPart(
                    rd.get("text", ""),
                    signature=rd.get("signature")))
            elif rtype == "reasoning.summary":
                turn.thinking.append(ir.ThinkingPart(rd.get("summary", "")))
            elif rtype == "reasoning.encrypted":
                # Encrypted reasoning — preserve as-is with the data as text
                # so it round-trips if echoed back to OpenRouter.
                turn.thinking.append(ir.ThinkingPart(
                    rd.get("data", ""),
                    signature=rd.get("id")))

        for tc in message.get("tool_calls") or []:
            raw_args = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                from wiwi.streaming.partial_json import _repair_truncated_json
                try:
                    args = json.loads(_repair_truncated_json(raw_args))
                except json.JSONDecodeError:
                    args = {}
            turn.tool_calls.append(ir.ToolUsePart(
                id=tc.get("id", ""), name=tc.get("function", {}).get("name", ""),
                args=args, raw_args=raw_args))

        fr = choice.get("finish_reason", "stop")
        # OpenRouter uses "error" for mid-stream failures; map to "stop" so
        # the client gets a graceful finish (the error was already surfaced).
        turn.stop_reason = {"stop": "stop", "length": "length", "tool_calls": "tool_call",
                            "content_filter": "content_filter",
                            "error": "stop"}.get(fr, "stop")

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
            chunk = orjson.loads(data)
        except json.JSONDecodeError:
            return []

        # OpenRouter mid-stream error: top-level ``error`` with
        # finish_reason: "error" in choices.  Emit a StreamError so the
        # gateway surfaces it to the client.
        top_error = chunk.get("error")
        choices = chunk.get("choices") or []
        if top_error:
            msg = top_error.get("message", "OpenRouter stream error")
            out: list[dl.IRStreamDelta] = [dl.StreamError(message=msg, kind="status")]
            if choices and choices[0].get("finish_reason") == "error":
                # Close any open tool calls before the error
                for open_idx in sorted(self._open_tool_indices):
                    if open_idx in self._pending_opens:
                        cid, cname = self._pending_opens.pop(open_idx)
                        out.append(dl.ToolCallOpen(index=open_idx, id=cid, name=cname))
                    out.append(dl.ToolCallClose(index=open_idx))
                self._open_tool_indices.clear()
                self._tool_names.clear()
                self._pending_opens.clear()
            return out

        out: list[dl.IRStreamDelta] = []

        # usage may ride in ANY chunk
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

        # OpenRouter streams reasoning via ``reasoning`` or ``reasoning_details``
        reasoning_text = delta.get("reasoning") or delta.get("reasoning_content")
        if reasoning_text:
            out.append(dl.ThinkingDelta(reasoning_text))

        for rd in delta.get("reasoning_details") or []:
            rtype = rd.get("type", "")
            if rtype == "reasoning.text":
                text = rd.get("text", "")
                sig = rd.get("signature")
                if text:
                    out.append(dl.ThinkingDelta(text, signature=sig))
            elif rtype == "reasoning.summary":
                summary = rd.get("summary", "")
                if summary:
                    out.append(dl.ThinkingDelta(summary))
            elif rtype == "reasoning.encrypted":
                # Encrypted reasoning — preserve as-is so it round-trips if
                # echoed back to OpenRouter (mirrors non-streaming decode).
                enc = rd.get("data", "")
                if enc:
                    out.append(dl.ThinkingDelta(enc, signature=rd.get("id")))

        tool_calls = delta.get("tool_calls") or []
        for i, tc in enumerate(tool_calls):
            idx = tc.get("index", i)
            fn = tc.get("function") or {}
            name_fragment = fn.get("name", "")
            if tc.get("id"):
                if idx in self._open_tool_indices:
                    out.append(dl.ToolCallClose(index=idx))
                self._open_tool_indices.add(idx)
                self._tool_names[idx] = name_fragment or ""
                # Defer emitting ToolCallOpen until the name is complete — the
                # first args fragment or finish signals name completion.
                self._pending_opens[idx] = (tc["id"], self._tool_names[idx])
            elif name_fragment and idx in self._open_tool_indices:
                self._tool_names[idx] = self._tool_names.get(idx, "") + name_fragment
                if idx in self._pending_opens:
                    cid, _ = self._pending_opens[idx]
                    self._pending_opens[idx] = (cid, self._tool_names[idx])
            if fn.get("arguments"):
                # Arguments arriving means the name is complete — flush the
                # deferred ToolCallOpen (if any) before the args delta.
                if idx in self._pending_opens:
                    cid, cname = self._pending_opens.pop(idx)
                    out.append(dl.ToolCallOpen(index=idx, id=cid, name=cname))
                out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=fn["arguments"]))

        fr = c.get("finish_reason")
        if fr:
            for open_idx in sorted(self._open_tool_indices):
                if open_idx in self._pending_opens:
                    cid, cname = self._pending_opens.pop(open_idx)
                    out.append(dl.ToolCallOpen(index=open_idx, id=cid, name=cname))
                out.append(dl.ToolCallClose(index=open_idx))
            self._open_tool_indices.clear()
            self._tool_names.clear()
            self._pending_opens.clear()
            out.append(dl.Finish({"stop": "stop", "length": "length",
                                  "tool_calls": "tool_call",
                                  "content_filter": "content_filter",
                                  "error": "stop"}.get(fr, "stop")))
        return out
