"""OpenAI adapter: chat completions API (also serves openai-compatible endpoints)."""

from __future__ import annotations

import json
from typing import Any

import orjson
import structlog

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef, coerce_args_fragment
from wiwi.streaming import deltas as dl

log = structlog.get_logger("wiwi.openai_adapter")


def _role_parts_to_content(
    messages: list[ir.Message],
    *,
    emit_per_message_reasoning: bool = True,
) -> list[dict[str, Any]]:
    """Encode IR messages as a list of OpenAI Chat wire messages.

    ``emit_per_message_reasoning`` controls whether ``reasoning`` /
    ``reasoning_content`` are written on history assistant messages. Only
    native OpenAI accepts these as input; strict OpenAI-compatible gateways
    (Palantir Foundry, etc.) reject with ``400 unrecognizedProperty`` when
    history carries them. The OpenAI adapter enables this for the OpenAI
    provider type and disables it for openai-compatible / openrouter / gmicloud.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            text = " ".join(p.text for p in m.parts if isinstance(p, ir.TextPart))
            out.append({"role": "system", "content": text})
            continue
        if m.role == "tool":
            for p in m.parts:
                if isinstance(p, ir.ToolResultPart):
                    if p.block_type != "tool_result":
                        # A provider-executed tool's result: it answers no entry
                        # in ``tool_calls``, so a role:"tool" message would be
                        # an orphan. Its payload is still what the model needs
                        # (search hits, discovered tool references), so fold it
                        # into the assistant turn as text.
                        out.append({"role": "assistant", "content": p.content})
                        continue
                    content: Any = f"[tool error] {p.content}" if p.is_error else p.content
                    if p.images:
                        # Multimodal tool result: OpenAI content-parts form.
                        parts_out: list[dict[str, Any]] = []
                        if p.content:
                            parts_out.append({"type": "text", "text": content})
                        for img in p.images:
                            url = img.url or f"data:{img.mime};base64,{img.b64}"
                            parts_out.append({"type": "image_url",
                                              "image_url": {"url": url}})
                        content = parts_out
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
            elif isinstance(p, ir.AudioPart):
                # input_audio round-trip: the chat codec decodes it into an
                # AudioPart, so re-emit it or an audio-only turn degrades to
                # content "" and the model never hears the audio.
                if content is None or isinstance(content, str):
                    content = ([{"type": "text", "text": content}] if content else [])
                fmt = (p.mime or "audio/wav").removeprefix("audio/")
                content.append({"type": "input_audio",
                                "input_audio": {"data": p.b64, "format": fmt}})
            elif isinstance(p, ir.DocumentPart):
                # Anthropic PDFs arrive as DocumentPart. Chat Completions has
                # no document block, but dropping it silently (the previous
                # behaviour) meant Claude Code attached a PDF and the model
                # answered as if no file were sent, with nothing in the logs.
                # OpenAI-compatible backends that DO accept documents take the
                # ``file`` content part, so emit that; a backend that rejects
                # it fails loudly instead of quietly answering a different
                # question (AUDIT #156).
                if content is None or isinstance(content, str):
                    content = ([{"type": "text", "text": content}] if content else [])
                if p.url:
                    content.append({"type": "file",
                                    "file": {"file_data": p.url}})
                elif p.b64:
                    content.append({"type": "file",
                                    "file": {"file_data": f"data:{p.mime};base64,{p.b64}"}})
            elif isinstance(p, ir.ToolUsePart):
                if p.builtin is not None:
                    # Provider-hosted call (web_search, tool_search, ...). No
                    # OpenAI-wire backend can host it and the client cannot
                    # dispatch it, so emitting a function call would leave a
                    # ``tool_calls`` entry that no ``role:"tool"`` message ever
                    # answers — OpenAI-compatible upstreams reject that history
                    # outright on the next turn. The paired result rides as a
                    # text part instead (see the ToolResultPart arm).
                    continue
                tool_calls.append({
                    "id": p.id, "type": "function",
                    "function": {"name": p.name,
                                 "arguments": p.raw_args or json.dumps(p.args)},
                })
            elif isinstance(p, ir.ThinkingPart):
                reasoning += p.text
            elif isinstance(p, ir.ToolResultPart):
                if p.block_type != "tool_result":
                    # A provider-executed tool's result (web_search_tool_result,
                    # tool_search_tool_result, ...). It answers a call that is
                    # NOT in ``tool_calls``, so a role:"tool" message would be
                    # an orphan — and this can sit in an ASSISTANT turn, which
                    # the role check below excluded entirely. Its payload is
                    # what the model needs (search hits, discovered tool
                    # references), so keep it as text on the current message.
                    if content is None or isinstance(content, str):
                        content = ([{"type": "text", "text": content}] if content
                                   else [])
                    content.append({"type": "text", "text": p.content})
                    continue
                if m.role != "user":
                    continue
                # Anthropic convention: tool results arrive as user-role
                # messages with tool_result content blocks. OpenAI expects
                # them as role=tool messages, so emit one per result.
                tool_content: Any = (f"[tool error] {p.content}"
                                     if p.is_error else p.content)
                if p.images:
                    parts_out = []
                    if p.content:
                        parts_out.append({"type": "text", "text": tool_content})
                    for img in p.images:
                        url = img.url or f"data:{img.mime};base64,{img.b64}"
                        parts_out.append({"type": "image_url",
                                          "image_url": {"url": url}})
                    tool_content = parts_out
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
        if reasoning and m.role == "assistant" and emit_per_message_reasoning:
            # Native OpenAI Chat accepts reasoning_content on history assistant
            # messages. Some strict OpenAI-compatible gateways reject
            # unrecognized fields on history — caller should pass
            # emit_per_message_reasoning=False in that case.
            msg["reasoning_content"] = reasoning
        out.append(msg)
    return out


class OpenAIAdapter:
    provider_type = "openai"
    force_stream = False  # set True on adapters whose upstream is streaming-only

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        h = {"Authorization": f"Bearer {key.secret}"}
        return h

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        base = base_url.rstrip("/")
        return f"{base}/chat/completions"

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        g = req.gen_params
        # Per-message reasoning_content is only safe to forward to providers
        # that accept it as input. Native OpenAI does; strict OpenAI-compatible
        # gateways (Palantir Foundry and others) reject history assistant
        # messages with `400 unrecognizedProperty=reasoning_content`. We also
        # exclude OpenRouter because its per-message handling differs (it uses
        # `reasoning_details` on the response side and rejects `reasoning` /
        # `reasoning_content` on input).
        ptype = deployment_params.get("provider_type")
        emit_per_message_reasoning = ptype not in {
            "openai-compatible", "gmicloud", "openrouter", "bai",
        }
        body: dict[str, Any] = {
            "model": model_id,
            "messages": _role_parts_to_content(
                req.messages,
                emit_per_message_reasoning=emit_per_message_reasoning,
            ),
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
        is_native_openai = ptype not in {
            "openai-compatible", "gmicloud", "nvidia-nim", "bai",
        }
        _VALID_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        # Resolve the effort the caller actually asked for. The three dialects
        # spell it differently — reasoning_effort (OpenAI), thinking_budget
        # (Anthropic's legacy budget) and effort (Anthropic's
        # output_config.effort, which is where Claude Code's /effort command,
        # --effort and CLAUDE_CODE_EFFORT_LEVEL land) — and
        # ``effective_reasoning_effort`` is the one place that reconciles them.
        # Reading only the first two meant an effort selection was dropped on
        # every non-Anthropic backend (AUDIT #156).
        effort = g.effective_reasoning_effort()
        if effort and is_native_openai and effort in _VALID_EFFORTS:
            # Only forward a KNOWN effort to a NATIVE OpenAI endpoint: a typo or
            # a future level this map has not learned must not reach the
            # upstream (instant 400), and compatible gateways reject the field
            # outright.
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
        encoded_tools = self._encode_tools(req)
        if encoded_tools:
            body["tools"] = encoded_tools
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
        # parallel_tool_calls=false on the OpenAI side. Deliberately OUTSIDE
        # the `if encoded_tools` guard: an Anthropic caller can serialize its
        # tool calls while every tool it declared was a provider-hosted
        # builtin (all dropped here), and the constraint must still reach the
        # backend — nesting it meant the request silently allowed concurrency
        # the caller had forbidden (AUDIT #158). An explicit
        # ``parallel_tool_calls`` still wins, matching the opencode adapter.
        if g.parallel_tool_calls is None and g.disable_parallel_tool_use is not None:
            body["parallel_tool_calls"] = not g.disable_parallel_tool_use
        if req.stream and req.stream_options_include_usage:
            body["stream_options"] = {"include_usage": True}
        for k, v in deployment_params.get("extra_body", {}).items():
            body.setdefault(k, v)
        # Standard chat params clients send that the IR doesn't model; forward
        # to OpenAI-shaped upstreams. drop_params=False forwards the rest raw.
        # The 2026 additions (verbosity, web_search_options, prediction, store,
        # metadata, prompt_cache_key, safety_identifier, modalities, audio,
        # logit_bias, service_tier) land here too.
        _STANDARD = {"frequency_penalty", "presence_penalty", "logprobs",
                     "top_logprobs", "user", "verbosity", "web_search_options",
                     "prediction", "store", "metadata", "prompt_cache_key",
                     "safety_identifier", "modalities", "audio", "logit_bias",
                     "service_tier"}
        for k, v in req.extras.items():
            if k in _STANDARD or not deployment_params.get("drop_params", True):
                body.setdefault(k, v)
        return body

    def _encode_tools(self, req: ir.Request) -> list[dict[str, Any]] | None:
        """Render IR tools as Chat Completions ``tools`` entries (or None).

        The base implementation emits function tools only: Chat Completions
        has no hosted-tool representation, so a provider-hosted builtin
        (e.g. web_search) is dropped with a warning rather than mangled into
        a function tool the upstream would reject. Subclasses that can host
        a builtin (OpenRouter) override this. The drop is capability-driven,
        independent of drop_params.
        """
        if not req.tools:
            return None
        out: list[dict[str, Any]] = []
        for t in req.tools:
            if t.builtin is not None:
                log.warning("dropping_unhostable_builtin_tool",
                            builtin=t.builtin, provider=self.provider_type)
                continue
            fn: dict[str, Any] = {"name": t.name, "description": t.description,
                                  "parameters": t.parameters_json_schema}
            # Forward strict mode (OpenAI structured outputs / Anthropic
            # strict tool use).
            if t.strict is not None:
                fn["strict"] = t.strict
            # Anthropic's ``input_examples`` has no Chat Completions field, and
            # an unknown key on a tool definition is a 400 on strict
            # OpenAI-compatible gateways. The examples are worth keeping — the
            # docs recommend them precisely for complex, format-sensitive
            # inputs — so render them into the description, which every backend
            # accepts. Dropping them silently made a tool that arrived with
            # worked examples look identical to one without.
            if t.input_examples:
                rendered = json.dumps(t.input_examples, ensure_ascii=False)
                fn["description"] = (
                    f"{t.description}\n\nExample inputs:\n{rendered}"
                    if t.description else f"Example inputs:\n{rendered}")
            out.append({"type": "function", "function": fn})
        return out or None

    # -- response decoding -----------------------------------------------------
    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = orjson.loads(body)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        # refusal carries the content-filter explanation when content is null;
        # surface it as the turn text so it survives crossing to other dialects.
        turn = ir.AssistantTurn(text=message.get("content")
                                or message.get("refusal") or "", raw=data)
        # Reasoning models may return reasoning_content in the message body
        # (DeepSeek, OpenRouter, and other OpenAI-compatible providers).
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            turn.thinking.append(ir.ThinkingPart(reasoning))
        for tc in message.get("tool_calls") or []:
            fn_tc = tc.get("function") or {}
            raw_args = fn_tc.get("arguments")
            if isinstance(raw_args, dict):
                # Args-as-object gateways: use the dict directly instead of
                # json.loads(TypeError) — a 500 on every replayed history.
                args = raw_args
                raw_args = json.dumps(raw_args)
            else:
                if not isinstance(raw_args, str):
                    # A truthy scalar (`true`, `5`) reaches here and
                    # ``json.loads`` raises TypeError — not JSONDecodeError —
                    # which the handler below does not catch, so the whole
                    # 200 response failed to decode (AUDIT #124). Treat any
                    # non-string as unparseable args.
                    raw_args = ""
                raw_args = raw_args or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    # Auto-repair truncated JSON instead of dropping to {}.
                    from wiwi.streaming.partial_json import _repair_truncated_json
                    try:
                        args = json.loads(_repair_truncated_json(raw_args))
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
            turn.tool_calls.append(ir.ToolUsePart(
                id=tc.get("id", ""), name=fn_tc.get("name", ""),
                args=args, raw_args=raw_args))
        fr = choice.get("finish_reason", "stop")
        turn.stop_reason = {"stop": "stop", "length": "length", "tool_calls": "tool_call",
                            # legacy function_call API: same meaning as tool_calls
                            "function_call": "tool_call",
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
        # Deferred ToolCallOpen per index: some OpenAI-compatible providers
        # (vLLM, etc.) fragment the function name across multiple deltas. We
        # buffer the open until the name is complete (first args fragment or
        # finish), then emit it with the full name so the client and every
        # wire encoder see the complete tool name instead of a partial one.
        self._pending_opens: dict[int, tuple[str, str]] = {}  # idx -> (id, name)
        # Indices where we synthesized an Open because the provider sent args
        # with no id (see decode_stream_event). A real id arriving later must
        # be adopted, not open a second tool call on the same index.
        self._synthesized_opens: set[int] = set()

    def reset(self) -> None:
        """Drop all per-stream state so the adapter can serve another stream.

        Adapters are stateless between *requests* but accumulate state while
        decoding a stream. A stream that dies before ``finish_reason`` leaves
        that state behind, so a reused adapter must be reset explicitly.
        """
        self._open_tool_indices.clear()
        self._tool_names.clear()
        self._pending_opens.clear()
        self._synthesized_opens.clear()

    def _flush_open_tools(self) -> list[dl.IRStreamDelta]:
        """Close every still-open tool call, flushing deferred Opens first.

        Used by the ``[DONE]`` arms: a stream that ends on the sentinel
        instead of a ``finish_reason`` chunk would otherwise leave its tool
        calls open, so the gateway synthesized ``stop`` for a turn that
        produced tool calls and the wire encoder emitted a ``tool_use`` block
        that never stopped (AUDIT #133). Returns ``[]`` when nothing is open,
        which keeps the plain-text ``[DONE]`` path byte-identical to before.
        """
        out: list[dl.IRStreamDelta] = []
        for open_idx in sorted(self._open_tool_indices):
            if open_idx in self._pending_opens:
                cid, cname = self._pending_opens.pop(open_idx)
                out.append(dl.ToolCallOpen(index=open_idx, id=cid, name=cname))
            out.append(dl.ToolCallClose(index=open_idx))
        self._open_tool_indices.clear()
        self._tool_names.clear()
        self._pending_opens.clear()
        self._synthesized_opens.clear()
        return out

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        if data == "[DONE]":
            # Flush before terminating: see _flush_open_tools (AUDIT #133).
            # A stream with no open tools still yields exactly [StreamEnd()],
            # so the gateway's round-15 synthesis for plain text is unchanged.
            out = self._flush_open_tools()
            if out:
                # Tool calls were delivered, so the stop reason is content-
                # derived, not "stop". Without this the gateway's
                # `finish is None` branch synthesized Finish("stop") and the
                # client's stop_reason disagreed with the tool_use blocks it
                # received.
                out.append(dl.Finish("tool_call"))
            out.append(dl.StreamEnd())
            return out
        try:
            chunk = orjson.loads(data)
        except json.JSONDecodeError:
            return []
        if not isinstance(chunk, dict):
            # Non-dict frame: ignore rather than crash on ``chunk.get``
            # (AUDIT #110).
            return []
        out: list[dl.IRStreamDelta] = []
        choices = chunk.get("choices") or []
        # usage may ride in ANY chunk — OpenAI/OpenRouter put it in the same
        # final chunk as choices+finish_reason. Parse it whenever present;
        # later cumulative values replace earlier ones.
        u = chunk.get("usage")
        if isinstance(u, dict):
            dp = u.get("prompt_tokens_details")
            dp = dp if isinstance(dp, dict) else {}
            dc = u.get("completion_tokens_details")
            dc = dc if isinstance(dc, dict) else {}
            out.append(dl.UsageFinal(
                prompt=u.get("prompt_tokens", 0), cached=dp.get("cached_tokens", 0),
                reasoning=dc.get("reasoning_tokens", 0),
                output=u.get("completion_tokens", 0)))
        if not choices:
            return out
        c = choices[0]
        if not isinstance(c, dict):
            # Non-dict choice must be skipped, not crash on ``c.get``
            # (AUDIT #110).
            return out
        delta = c.get("delta")
        if not isinstance(delta, dict):
            # A truthy non-dict delta (int/string/bool/list) has nothing to
            # decode; decode with an empty delta (a finish-only chunk carries
            # no delta key at all and must still reach the finish handling —
            # AUDIT #154).
            delta = {}
        # Typed-wrong content/reasoning must be dropped, not forwarded as a
        # TextDelta/ThinkingDelta — the deltas contractually carry ``str``,
        # and a non-str breaks the pump (len()) or the wire encoder
        # (AUDIT #154).
        if isinstance(delta.get("content"), str) and delta["content"]:
            out.append(dl.TextDelta(delta["content"]))
        if isinstance(delta.get("reasoning_content"), str) and delta["reasoning_content"]:
            out.append(dl.ThinkingDelta(delta["reasoning_content"]))
        elif isinstance(delta.get("reasoning"), str) and delta["reasoning"]:
            out.append(dl.ThinkingDelta(delta["reasoning"]))
        tool_calls = delta.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = []
        for i, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                # Malformed entry (null/scalar): skip, not crash (AUDIT #154).
                continue
            idx = tc.get("index", i)
            fn = tc.get("function")
            fn = fn if isinstance(fn, dict) else {}
            name_fragment = fn.get("name", "")
            if tc.get("id"):
                if idx in self._synthesized_opens:
                    # The provider omitted the id on the first chunk, so we
                    # synthesized an Open for this index; the real id has now
                    # arrived. Adopt it instead of closing/re-opening — two
                    # Opens for one index would break the contract. The Open
                    # was already emitted, so record the id for later frames
                    # (tool_result correlation) and keep the call open.
                    self._synthesized_opens.discard(idx)
                    self._tool_names[idx] = name_fragment or ""
                    if fn.get("arguments"):
                        out.append(dl.ToolCallArgsDelta(
                            index=idx,
                            args_fragment=coerce_args_fragment(fn["arguments"])))
                    continue
                # a new tool call opening on the same index closes the previous one
                if idx in self._open_tool_indices:
                    # The superseded call's Open may still be deferred (id
                    # seen, args not yet). Flush it before closing, or the
                    # stream carries a Close for an index that never opened.
                    if idx in self._pending_opens:
                        cid, cname = self._pending_opens.pop(idx)
                        out.append(dl.ToolCallOpen(index=idx, id=cid, name=cname))
                    out.append(dl.ToolCallClose(index=idx))
                self._open_tool_indices.add(idx)
                # Accumulate name: some providers send the full name on the
                # first chunk, others fragment it across subsequent deltas.
                self._tool_names[idx] = name_fragment or ""
                # Defer emitting ToolCallOpen until the name is complete — the
                # first args fragment or finish signals name completion.
                self._pending_opens[idx] = (tc["id"], self._tool_names[idx])
            elif name_fragment and idx in self._open_tool_indices:
                # Name fragment on a subsequent delta (no id): accumulate.
                self._tool_names[idx] = self._tool_names.get(idx, "") + name_fragment
                if idx in self._pending_opens:
                    # Update the deferred open with the accumulated name.
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
                    # Open -> ArgsDelta* -> Close contract, so synthesize the
                    # Open first. If a real id shows up later it is ignored —
                    # this index is already open — keeping exactly one Open.
                    self._open_tool_indices.add(idx)
                    self._tool_names[idx] = name_fragment or ""
                    self._synthesized_opens.add(idx)
                    # Carry the name fragment we just stored: emitting an
                    # empty name left the client with a tool_use block whose
                    # name is "", so it could not dispatch the call at all
                    # (AUDIT #135).
                    out.append(dl.ToolCallOpen(index=idx, id="",
                                               name=self._tool_names[idx]))
                args_val = coerce_args_fragment(fn["arguments"])
                out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=args_val))
        fr = c.get("finish_reason")
        if fr:
            # close ALL still-open tool calls before finishing (parallel tools)
            for open_idx in sorted(self._open_tool_indices):
                # Flush any deferred open (tool call with a name but no args).
                if open_idx in self._pending_opens:
                    cid, cname = self._pending_opens.pop(open_idx)
                    out.append(dl.ToolCallOpen(index=open_idx, id=cid, name=cname))
                out.append(dl.ToolCallClose(index=open_idx))
            self._open_tool_indices.clear()
            self._tool_names.clear()
            self._pending_opens.clear()
            # Clear the synthesized-open markers too: they are per-call, and a
            # stale index makes a *later* tool call that reuses it take the
            # "adopt the real id" branch — emitting ToolCallArgsDelta with no
            # preceding ToolCallOpen, which the encoders drop (AUDIT #129).
            self._synthesized_opens.clear()
            out.append(dl.Finish({"stop": "stop", "length": "length",
                                  "tool_calls": "tool_call",
                                  "function_call": "tool_call",
                                  "content_filter": "content_filter"}.get(fr, "stop")))
        return out

