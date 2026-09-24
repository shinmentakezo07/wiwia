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
from wiwi.ir import translation as tr
from wiwi.ir import types as ir
from wiwi.providers.base import (
    ProviderKeyRef,
    as_dict,
    as_list,
    as_str,
    coerce_args_fragment,
)
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl


def _token_count(value: Any) -> int:
    """A usage counter as the ``int`` the IR/deltas require.

    ``.get(k, 0)`` defaults only a *missing* key: a JSON ``null`` (or any
    typed-wrong value) passed straight through, and the poisoned count reached
    ``core/gateway.py``'s ``u.prompt + u.output`` and ``u.cached > 0`` — both
    of which raise, mid-stream, after the client already had a 200 (AUDIT
    #194). A ``bool`` is not a token count, so ``ir.coerce_int``'s rejection
    is kept.
    """
    return ir.coerce_int(value) or 0


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
            # Anthropic's worked examples have no OpenRouter field; render them
            # into the description (see the OpenAI adapter for the rationale).
            if t.input_examples:
                rendered = json.dumps(t.input_examples, ensure_ascii=False)
                fn["description"] = (
                    f"{t.description}\n\nExample inputs:\n{rendered}"
                    if t.description else f"Example inputs:\n{rendered}")
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
        if (isinstance(req.tool_choice, ir.ToolChoiceNamed)
                and any(t.builtin == req.tool_choice.name for t in req.tools)):
            wire_type = bt.wire_type_for("openrouter", req.tool_choice.name)
            if wire_type is not None:
                body["tool_choice"] = {"type": wire_type}

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

        # A direct token budget is more precise than a named level, so it wins
        # — OpenRouter can express it exactly as reasoning.max_tokens, whereas
        # an effort name would be rounded through the global effort→budget map.
        # ``effort`` reconciles the named spellings (reasoning_effort and
        # Anthropic's output_config.effort); reading only the raw
        # reasoning_effort dropped an effort-only request (AUDIT #156).
        reasoning_obj: dict[str, Any] | None = None
        effort = g.effective_reasoning_effort()

        if g.thinking_budget == 0:
            # Zero budget is the documented thinking-off value; the sibling
            # adapters (Anthropic/Gemini/OpenAI) honor it. Clamping to the
            # 1024 minimum instead switched thinking ON for an explicit
            # disable.
            reasoning_obj = {"enabled": False}
        elif g.thinking_budget is not None:
            # Anthropic-style token budget -> OpenRouter reasoning.max_tokens.
            # OpenRouter enforces a minimum of 1024 for Anthropic models.
            budget = max(g.thinking_budget, 1024)
            reasoning_obj = {"max_tokens": budget}
        elif effort == "none":
            # Explicitly disable reasoning
            reasoning_obj = {"enabled": False}
        elif isinstance(effort, str) and effort in {
            "max", "xhigh", "high", "medium", "low", "minimal",
        }:
            # OpenRouter documents a closed effort enum. A client typo or a
            # typed-wrong value must not become an upstream JSON-schema 400.
            reasoning_obj = {"effort": effort}

        if reasoning_obj is not None:
            body["reasoning"] = reasoning_obj

        # Reasoning is part of the visible output-token budget. Anthropic
        # rejects a completion limit that is not strictly above its reasoning
        # budget; keep the same invariant for OpenRouter's unified parameter.
        if reasoning_obj is not None and "max_tokens" in reasoning_obj:
            limit = body.get("max_completion_tokens")
            budget = reasoning_obj["max_tokens"]
            if isinstance(limit, int) and limit <= budget:
                body["max_completion_tokens"] = budget + 1024
        # OpenRouter supports stream_options.include_usage; keep it only if
        # the client explicitly requested it (the OpenAI adapter already guards
        # this, but we double-check here for safety).
        if body.get("stream") and not req.stream_options_include_usage:
            body.pop("stream_options", None)

        return body

    # -- decode: extract reasoning_details and mid-stream errors -----------------

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = orjson.loads(body)
        # ``or [{}]`` / ``.get(k, {})`` default only a *missing* key, so an
        # explicit null or a typed-wrong value raised AttributeError on the
        # next read — a retryable 502 charged to key/deployment health for a
        # frame carrying no semantics (AUDIT #247).
        _choices = as_list(data.get("choices"))
        choice = as_dict(_choices[0]) if _choices else {}
        message = as_dict(choice.get("message"))
        content = message.get("content")
        if isinstance(content, list):
            # OpenRouter permits assistant content arrays. The IR carries text
            # only, so concatenate the text parts and ignore other media/output
            # types rather than assigning a list to ``AssistantTurn.text``.
            content = "".join(
                part.get("text") or ""
                for part in content
                if isinstance(part, dict) and part.get("type") in ("text", "output_text")
                and isinstance(part.get("text"), str)
            )
        elif not isinstance(content, str):
            content = message.get("refusal") if isinstance(message.get("refusal"), str) else ""
        turn = ir.AssistantTurn(text=content, raw=data)

        # OpenRouter returns reasoning in ``reasoning`` (string) or
        # ``reasoning_details`` (array of structured objects).  The string
        # form is the common case; the array form carries encrypted/summary
        # blocks that we flatten into ThinkingPart.
        reasoning_str = message.get("reasoning") or message.get("reasoning_content")
        if isinstance(reasoning_str, str) and reasoning_str:
            turn.thinking.append(ir.ThinkingPart(reasoning_str))
        for rd in as_list(message.get("reasoning_details")):
            if not isinstance(rd, dict):
                # A malformed reasoning_details entry must be skipped, not
                # crash the decode (AUDIT #110).
                continue
            rtype = rd.get("type", "")
            if rtype == "reasoning.text":
                text = rd.get("text")
                signature = rd.get("signature")
                turn.thinking.append(ir.ThinkingPart(
                    text if isinstance(text, str) else "",
                    signature=signature if isinstance(signature, str) else None))
            elif rtype == "reasoning.summary":
                summary = rd.get("summary")
                turn.thinking.append(ir.ThinkingPart(
                    summary if isinstance(summary, str) else ""))
            elif rtype == "reasoning.encrypted":
                encrypted = rd.get("data")
                identifier = rd.get("id")
                turn.thinking.append(ir.ThinkingPart(
                    encrypted if isinstance(encrypted, str) else "",
                    signature=identifier if isinstance(identifier, str) else None))

        for tc in as_list(message.get("tool_calls")):
            if not isinstance(tc, dict):
                continue  # malformed entry (AUDIT #110)
            fn = as_dict(tc.get("function"))
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, dict):
                args = raw_args
                raw_args = json.dumps(raw_args)
            else:
                if not isinstance(raw_args, str):
                    raw_args = ""
                try:
                    parsed = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError):
                    from wiwi.streaming.partial_json import _repair_truncated_json
                    try:
                        parsed = json.loads(_repair_truncated_json(raw_args))
                    except (json.JSONDecodeError, TypeError):
                        parsed = {}
                args = parsed if isinstance(parsed, dict) else {}
            turn.tool_calls.append(ir.ToolUsePart(
                id=as_str(tc.get("id")), name=as_str(fn.get("name")),
                args=args, raw_args=raw_args))


        fr = choice.get("finish_reason", "stop")
        # OpenRouter uses "error" for mid-stream failures; the shared map
        # already maps it to "stop" (the error was surfaced separately).
        # AUDIT #270: was an inline map narrower than the shared one, so a
        # non-standard tool-call spelling fell through to "stop".
        turn.stop_reason = tr.normalize_finish_reason(fr)

        u = as_dict(data.get("usage"))
        details_p = as_dict(u.get("prompt_tokens_details"))
        details_c = as_dict(u.get("completion_tokens_details"))
        turn.usage = ir.Usage(
            prompt_tokens=_token_count(u.get("prompt_tokens")),
            completion_tokens=_token_count(u.get("completion_tokens")),
            cached_tokens=_token_count(details_p.get("cached_tokens")),
            reasoning_tokens=_token_count(details_c.get("reasoning_tokens")),
        )
        return turn

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        if data == "[DONE]":
            # A [DONE]-terminated stream may still hold open tool calls: some
            # providers end with the sentinel instead of a finish_reason
            # chunk. Emitting a bare StreamEnd left them open, so the gateway
            # saw no Finish and synthesized `stop` for a turn that produced
            # tool calls, and the client was left with an unterminated
            # tool_use block (AUDIT #133). Flush first — a stream with nothing
            # open still yields exactly `[StreamEnd()]`, preserving the
            # gateway's round-15 synthesis for plain text streams.
            out = self._flush_open_tools()
            if out:
                # Tool calls were delivered, so the stop reason is content-
                # derived, not "stop". Without this the gateway's
                # `finish is None` branch synthesized Finish("stop") and the
                # client's stop_reason disagreed with the tool_use blocks it
                # received — the same fourth copy-derived site as NIM.
                out.append(dl.Finish("tool_call"))
            out.append(dl.StreamEnd())
            return out
        try:
            chunk = orjson.loads(data)
        except json.JSONDecodeError:
            return []
        if not isinstance(chunk, dict):
            # A non-dict frame (null/string/array) must be ignored, not crash
            # on ``chunk.get`` (AUDIT #110).
            return []

        # OpenRouter mid-stream error: top-level ``error`` with
        # finish_reason: "error" in choices.  Emit a StreamError so the
        # gateway surfaces it to the client.
        top_error = chunk.get("error") if isinstance(chunk.get("error"), dict) else None
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            # Truthy non-list (``5``/``true``/dict) used to survive ``or []``
            # and crash on ``choices[0]`` (AUDIT #224).
            choices = []
        if top_error:
            # ``top_error.get("message", ...)`` defaults only a *missing* key,
            # so a null message reached the client as a contract-invalid
            # ``"message": null`` frame. Coerce like ClineAdapter's arm
            # (AUDIT #200).
            msg = str(top_error.get("message") or "OpenRouter stream error")
            out: list[dl.IRStreamDelta] = [dl.StreamError(message=msg, kind="status")]
            if choices and isinstance(choices[0], dict) \
                    and choices[0].get("finish_reason") == "error":
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

        # usage may ride in ANY chunk. ``if u:`` alone let a truthy non-dict
        # (a string, a list) reach ``u.get`` and raise AttributeError out of
        # the decoder, where its siblings gate with ``isinstance(u, dict)``
        # (AUDIT #197).
        u = chunk.get("usage")
        if isinstance(u, dict):
            dp = u.get("prompt_tokens_details")
            dp = dp if isinstance(dp, dict) else {}
            dc = u.get("completion_tokens_details")
            dc = dc if isinstance(dc, dict) else {}
            out.append(dl.UsageFinal(
                prompt=_token_count(u.get("prompt_tokens")),
                cached=_token_count(dp.get("cached_tokens")),
                reasoning=_token_count(dc.get("reasoning_tokens")),
                output=_token_count(u.get("completion_tokens"))))

        if not choices:
            return out
        c = choices[0]
        if not isinstance(c, dict):
            # Non-dict choice must be skipped, not crash on ``c.get``
            # (AUDIT #110).
            return out
        delta = c.get("delta")
        if not isinstance(delta, dict):
            # A truthy non-dict delta has nothing to decode; decode with an
            # empty delta (finish-only chunks carry no delta key — AUDIT
            # #154).
            delta = {}

        # Typed-wrong content/reasoning is dropped, not forwarded as a
        # non-str delta (contract break — AUDIT #154).
        if isinstance(delta.get("content"), str) and delta["content"]:
            out.append(dl.TextDelta(delta["content"]))

        # OpenRouter streams reasoning via ``reasoning`` or ``reasoning_details``
        reasoning_text = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning_text, str) and reasoning_text:
            out.append(dl.ThinkingDelta(reasoning_text))

        # ``or []`` defaults only a *falsy* value, so a truthy non-list
        # (``5``, ``true``) survived and raised ``TypeError`` mid-stream,
        # which the pump routes to a provider cooldown (AUDIT #231).
        for rd in as_list(delta.get("reasoning_details")):
            if not isinstance(rd, dict):
                continue
            rtype = rd.get("type", "")
            if rtype == "reasoning.text":
                text = rd.get("text")
                signature = rd.get("signature")
                if isinstance(text, str) and text:
                    out.append(dl.ThinkingDelta(
                        text, signature=signature if isinstance(signature, str) else None))
            elif rtype == "reasoning.summary":
                summary = rd.get("summary")
                if isinstance(summary, str) and summary:
                    out.append(dl.ThinkingDelta(summary))
            elif rtype == "reasoning.encrypted":
                encrypted = rd.get("data")
                identifier = rd.get("id")
                if isinstance(encrypted, str) and encrypted:
                    out.append(dl.ThinkingDelta(
                        encrypted, signature=identifier if isinstance(identifier, str) else None))


        tool_calls = delta.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = []
        for i, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                # Malformed entry (null/scalar): skip, not crash (AUDIT #154).
                continue
            idx = tc.get("index", i)
            if not isinstance(idx, int) or isinstance(idx, bool):
                idx = i
            fn = as_dict(tc.get("function"))
            name_fragment = as_str(fn.get("name"))
            call_id = as_str(tc.get("id"))
            if call_id:
                if idx in self._synthesized_opens:
                    # The id was missing on the first chunk, so an Open was
                    # synthesized for this index; the real id has now arrived.
                    # Adopt it instead of closing and re-opening — two Opens
                    # for one index breaks the strict nesting contract and the
                    # encoder emits two tool_use blocks for one call. Mirrors
                    # OpenAIAdapter (AUDIT #135).
                    self._synthesized_opens.discard(idx)
                    self._tool_names[idx] = name_fragment
                    if fn.get("arguments"):
                        out.append(dl.ToolCallArgsDelta(
                            index=idx,
                            args_fragment=coerce_args_fragment(fn["arguments"])))
                    continue
                if idx in self._open_tool_indices:
                    # The superseded call's Open may still be deferred (id
                    # seen, args not yet). Flush it before closing, or the
                    # stream carries a Close for an index that never opened
                    # and the encoder silently drops it (AUDIT #74).
                    if idx in self._pending_opens:
                        cid, cname = self._pending_opens.pop(idx)
                        out.append(dl.ToolCallOpen(index=idx, id=cid, name=cname))
                    out.append(dl.ToolCallClose(index=idx))
                self._open_tool_indices.add(idx)
                self._tool_names[idx] = name_fragment
                # Defer emitting ToolCallOpen until the name is complete — the
                # first args fragment or finish signals name completion.
                self._pending_opens[idx] = (call_id, self._tool_names[idx])
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
                elif idx not in self._open_tool_indices:
                    # Some OpenAI-compatible providers send `arguments` on the
                    # very first tool chunk with no `id` (it may arrive on a
                    # later chunk, or never). Emitting args with no preceding
                    # Open violates the strictly nested
                    # Open -> ArgsDelta* -> Close contract, so every encoder
                    # silently drops them and the tool call vanishes. The base
                    # OpenAIAdapter synthesizes the Open here; OpenRouter
                    # overrode the whole method without this branch, so the
                    # two adapters diverged on identical input (AUDIT #135).
                    self._open_tool_indices.add(idx)
                    self._tool_names[idx] = name_fragment or ""
                    self._synthesized_opens.add(idx)
                    # Carry the name fragment we just stored — an empty name
                    # leaves the client with a tool_use block it cannot
                    # dispatch (AUDIT #135).
                    out.append(dl.ToolCallOpen(index=idx, id="",
                                               name=self._tool_names[idx]))
                out.append(dl.ToolCallArgsDelta(
                    index=idx,
                    args_fragment=coerce_args_fragment(fn["arguments"])))

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
            # Must clear alongside the other three: a stale index makes a later
            # tool call that reuses it take the "adopt the real id" branch and
            # emit ToolCallArgsDelta with no preceding ToolCallOpen, which every
            # encoder drops (AUDIT #129). The base class clears it and NIM
            # inherits that; this override re-implemented the sweep without it
            # (AUDIT #156).
            self._synthesized_opens.clear()
            # AUDIT #270: shared map (handles the non-standard spellings and
            # maps OpenRouter's "error" to "stop" as this inline copy did).
            out.append(dl.Finish(tr.normalize_finish_reason(fr)))
        return out
