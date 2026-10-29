"""Google Gemini adapter: generateContent REST + alt=sse streaming."""

from __future__ import annotations

import itertools
import json
from typing import Any

import orjson
import structlog

from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.providers.base import (
    ProviderKeyRef,
    WarningMixin,
    as_dict,
    as_list,
    as_str,
    unhonoured_platform_params,
)
from wiwi.streaming import deltas as dl

log = structlog.get_logger("wiwi.gemini_adapter")


# Process-wide, monotonically increasing counter used to make Gemini's
# synthetic tool-call ids unique ACROSS requests.
#
# Gemini's native ``functionCall`` parts carry no id, so one must be
# synthesized. A per-stream index produced ``call_<name>_<n>`` — and turn 1's
# ``call_get_weather_0`` was byte-identical to turn 2's, so anything that
# correlates tool calls by id (a client's own state machine, our logs, a
# replay tool) silently merged separate calls. A process-wide counter is the
# cheap fix; ``itertools.count`` is atomic under the GIL for this purpose and
# needs no lock.
_TOOL_ID_SEQ = itertools.count()


def _synthetic_tool_id(name: str) -> str:
    """A unique-enough id for a Gemini function input, which carries none.

    Keeps the tool name in the id (so logs and traces stay readable) and
    appends a process-global counter, so two turns that call the same tool get
    distinct ids.
    """
    return f"call_{name or 'x'}_{next(_TOOL_ID_SEQ)}"

def _token_count(value: Any) -> int:
    """A Gemini usage counter as the ``int`` the IR requires.

    ``usageMetadata`` is typed-wrong more often than one expects: Gemini 2.5
    and Vertex attach it to *every* chunk, and a proxy or a SAFETY-truncated
    candidate can serialize a counter as ``null``. The unguarded
    ``candidatesTokenCount + thoughtsTokenCount`` sum raised ``TypeError`` out
    of the decoder itself (AUDIT #195), unlike its sibling reads. A ``bool`` is
    not a token count, so ``ir.coerce_int``'s rejection is kept.
    """
    return ir.coerce_int(value) or 0


# Gemini finishReason -> IR stop reason. One owner for both the sync and the
# stream arm (AUDIT #365): the two arms previously carried identical inline
# copies of this dict, and neither guarded the key, so a typed-wrong
# ``finishReason`` (a list or dict from a proxy) raised ``TypeError`` out of the
# decoder — which the gateway turns into a retryable 502 and charges against a
# healthy deployment's cooldown. Every other adapter routes through
# ``ir.translation.normalize_finish_reason``, total by construction; this map
# keeps Gemini's richer vocabulary and is guarded the same way at each lookup
# (non-str -> "" -> "stop").
_FINISH_MAP: dict[str, ir.StopReason] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED": "content_filter",
    "SPII": "content_filter",
}


class GeminiAdapter(WarningMixin):
    provider_type = "gemini"

    def __init__(self) -> None:
        self._init_warnings()
        self._started = False
        self._saw_function_call = False
        self._tool_seq = 0
        # A stream's terminal tail (UsageFinal + Finish + StreamEnd) may be
        # emitted exactly once (AUDIT #161).
        self._saw_tail = False
        # Advisory translation warnings for THIS request, drained by the
        # gateway into ctx.metadata (see base.take_adapter_warnings). Reset
        # with the rest of the per-request state so a previous request's
        # warning never rides along on the next one.
        self._reset_warnings()

    def reset(self) -> None:
        """Drop per-stream state so the adapter can serve another stream."""
        self._started = False
        self._saw_function_call = False
        self._tool_seq = 0
        self._saw_tail = False
        self._reset_warnings()

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        return {}  # key goes in querystring

    def realtime_url(self, base_url: str) -> str | None:
        """No Realtime-style WebSocket surface on this provider.

        The /v1/realtime route reads this and answers 501 rather than dialling
        a URL that does not exist, so a client learns the provider cannot serve
        a session instead of hanging on a connection that will never open.
        """
        return None

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        base = base_url.rstrip("/") or "https://generativelanguage.googleapis.com/v1beta"
        method = "streamGenerateContent?alt=sse&key=" if stream else "generateContent?key="
        return f"{base}/models/{model_id}:{method}"

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        g = req.gen_params
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        # Gemini requires functionResponse.name to be the *function* name;
        # resolve it from the matching ToolUsePart in history.
        tool_names: dict[str, str] = {}
        for m in req.messages:
            for p in m.parts:
                if isinstance(p, ir.ToolUsePart):
                    tool_names[p.id] = p.name
        seen_non_system = False
        for m in req.messages:
            if m.role == "system" and not seen_non_system:
                system_parts.extend(p.text for p in m.parts if isinstance(p, ir.TextPart))
                continue
            if m.role == "system":
                # Mid-conversation system message. ``contents`` accepts only
                # user/model roles, so unlike Anthropic it cannot stay its own
                # entry: fold it in place as explicitly-marked user text. The
                # hoist used to move it to the front, destroying its position
                # relative to the turns around it (AUDIT #328). The merge at
                # the tail folds it into the adjacent user content when one
                # precedes it, and preserves its position after a model turn.
                role = "user"
                parts = [{"text": f"[system]\n{p.text}"}
                         for p in m.parts
                         if isinstance(p, ir.TextPart) and p.text]
                if parts:
                    if contents and contents[-1]["role"] == role:
                        contents[-1]["parts"].extend(parts)
                    else:
                        contents.append({"role": role, "parts": parts})
                continue
            seen_non_system = True
            role = "model" if m.role == "assistant" else "user"
            parts: list[dict[str, Any]] = []
            for p in m.parts:
                if isinstance(p, ir.ThinkingPart):
                    # Preserve prior reasoning across turns. Without this the
                    # part silently vanished, breaking cross-dialect multi-turn
                    # continuity (AUDIT #102). Gemini expects it back as a
                    # thought-flagged text part, signature included.
                    if p.block_type == "redacted_thinking":
                        # Opaque Anthropic blob: Gemini cannot read it back and
                        # the old arm sent ``{"text": "", "thought": true}`` —
                        # an empty part that carried nothing. Dropped, and the
                        # caller told, like the audio arm below.
                        self.warn(
                            "a redacted thinking block was dropped: Gemini "
                            "cannot replay encrypted reasoning")
                        continue
                    entry: dict[str, Any] = {"text": p.text, "thought": True}
                    if p.signature:
                        entry["thoughtSignature"] = p.signature
                    parts.append(entry)
                elif isinstance(p, ir.ImagePart):
                    if p.file_id:
                        # Files-API reference: Gemini has no spelling for a
                        # foreign provider's file id. Reported, not silent.
                        self.warn(
                            "a Files-API image reference was dropped: Gemini "
                            "has no equivalent")
                    elif p.b64:
                        parts.append({"inline_data": {"mime_type": p.mime, "data": p.b64}})
                    elif p.url:
                        parts.append({"file_data": {"file_uri": p.url}})
                elif isinstance(p, ir.TextPart):
                    parts.append({"text": p.text})
                elif isinstance(p, ir.DocumentPart):
                    # PDFs arrive as DocumentPart. Gemini takes documents as
                    # inline/file data exactly like images; dropping them (the
                    # previous behaviour) meant a Claude Code PDF attachment
                    # reached the model as nothing at all (AUDIT #156).
                    if p.file_id:
                        # No b64/url to ride inline_data/file_data: a foreign
                        # file id has no Gemini spelling. Reported, not silent
                        # (same rule as the ImagePart arm above).
                        self.warn(
                            "a Files-API document reference was dropped: "
                            "Gemini has no equivalent")
                    elif p.b64:
                        parts.append({"inline_data": {"mime_type": p.mime, "data": p.b64}})
                    elif p.url:
                        parts.append({"file_data": {"file_uri": p.url}})
                    # The document's title and context have no Gemini
                    # spelling. They are real information the caller
                    # supplied — a PDF named "report.pdf" with the context
                    # "Q3 revenue" — and dropping them silently made two
                    # different attachments indistinguishable to the model
                    # (AUDIT #499). The drop is reported to the caller.
                    if p.name or p.context:
                        self.warn(
                            "a document's title/context was dropped: Gemini "
                            "has no field for it")
                elif isinstance(p, ir.AudioPart):
                    # Gemini's ``inline_data`` carries audio bytes, so an
                    # audio part COULD ride it. But the inbound dialects'
                    # audio shapes differ (OpenAI's ``input_audio`` is a
                    # separate content type, and Gemini's inline audio has
                    # its own limits), so the part is dropped rather than
                    # guessed at — and, unlike before this branch, the
                    # caller is told (AUDIT #499).
                    log.warning("dropping_unsupported_audio_part",
                                provider=self.provider_type, mime=p.mime)
                    self.warn(
                        "an audio attachment was dropped: this provider does "
                        "not accept the inbound audio shape")
                elif isinstance(p, ir.ToolUsePart):
                    if p.builtin is not None:
                        # Provider-hosted call (web_search, tool_search, ...).
                        # Gemini cannot host it and the client cannot dispatch
                        # it, so a functionCall here is a phantom the model
                        # will try to use and never get a result for. Its
                        # result rides as text below.
                        continue
                    parts.append({"functionCall": {"name": p.name, "args": p.args}})
                elif isinstance(p, ir.ToolResultPart):
                    if p.block_type != "tool_result":
                        # A provider-executed tool's result: no functionCall
                        # exists for it, so a functionResponse would be an
                        # orphan (Gemini rejects a response with no call). Keep
                        # the payload the model needs as text.
                        parts.append({"text": p.content})
                        continue
                    try:
                        import json as _j
                        resp = _j.loads(p.content)
                    except (TypeError, ValueError):
                        resp = {"result": p.content}
                    fname = (tool_names.get(p.tool_use_id)
                             or p.tool_use_id.removeprefix("call_"))
                    parts.append({"functionResponse": {"name": fname,
                                                       "response": resp}})
                    if p.images:
                        # functionResponse could carry image parts
                        # (inline_data), but the multimodal-in-response
                        # support matrix is model-specific and rejecting an
                        # inline blob mid-history is worse than losing it.
                        # Reported, not silent (AUDIT #499 class).
                        self.warn(
                            "tool-result images were dropped: this provider "
                            "does not reliably accept them in a function "
                            "response")
            if parts:
                if contents and contents[-1]["role"] == role:
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": role, "parts": parts})
        body: dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        gen: dict[str, Any] = {}
        mt = g.max_tokens or deployment_params.get("max_tokens")
        if mt:
            gen["maxOutputTokens"] = mt
        if g.temperature is not None:
            gen["temperature"] = g.temperature
        if g.top_p is not None:
            gen["topP"] = g.top_p
        if g.top_k is not None:
            # Gemini takes topK natively; the IR field documents this surface.
            gen["topK"] = g.top_k
        if g.stop:
            gen["stopSequences"] = g.stop
        if g.seed is not None:
            # Gemini's generationConfig has no seed field. Reported to the
            # caller, who set it (AUDIT #498).
            self.warn("seed has no Gemini equivalent and was dropped")
        # OpenAI-2026 platform params the caller set (logit_bias,
        # frequency_penalty, service_tier, …). Gemini has no such fields in
        # generationConfig, so the request went out with them ignored. The
        # caller is told which ones did not apply (AUDIT #501).
        for name in unhonoured_platform_params(req.extras):
            self.warn(
                f"'{name}' has no Gemini equivalent and was dropped")
        if g.response_format and g.response_format.type in ("json_object",
                                                            "json_schema"):
            gen["responseMimeType"] = "application/json"
        if g.response_format and g.response_format.type == "json_schema":
            # ``json_schema`` has a native Gemini spelling: ``responseSchema``
            # in ``generationConfig``. Until now only ``json_object`` was
            # handled, so a caller asking for a schema-constrained object
            # got prose back — the constraint was dropped with no error
            # anywhere, and the caller had no way to tell (AUDIT #496).
            #
            # Gemini's schema dialect is OpenAPI-ish rather than JSON-Schema,
            # but the common subset (type/properties/required/items/enum)
            # is shared, and a schema Gemini cannot express is a 400 there
            # rather than a silent downgrade — which is the honest failure:
            # the caller learns the schema was not honoured.
            schema = g.response_format.json_schema
            if isinstance(schema, dict) and schema:
                gen["responseSchema"] = schema
            elif schema is not None:
                self.warn(
                    "the response schema was dropped: it is not a JSON object "
                    "and Gemini has no equivalent")
            if g.response_format.name:
                gen["responseSchema"] = {
                    "title": g.response_format.name,
                    **(schema if isinstance(schema, dict) else {}),
                }
        # Map reasoning effort to Gemini's thinkingConfig (reasoning token budget)
        thinking_budget = g.effective_thinking_budget()
        if g.thinking_budget == 0 or g.reasoning_effort == "none":
            # Explicit disable (either spelling): Gemini honors budget 0.
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        elif thinking_budget is not None:
            gen["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        if gen:
            body["generationConfig"] = gen
        if req.tools:
            # Function tools and hosted builtins are separate tool entries:
            # builtins render as their native key ({"google_search": {}}) —
            # Gemini takes no config for search, so builtin_config is dropped.
            decls = []
            for t in req.tools:
                if t.builtin is not None:
                    continue
                desc = t.description
                if t.input_examples:
                    # Anthropic tool examples: no functionDeclarations field,
                    # but the worked examples are exactly what sells a complex
                    # schema to the model — render into the description, the
                    # same carrier the OpenAI adapter uses. Dropping them
                    # silently lost them only on this route.
                    rendered = json.dumps(t.input_examples, ensure_ascii=False)
                    desc = (f"{t.description}\n\nExample inputs:\n{rendered}"
                            if t.description else f"Example inputs:\n{rendered}")
                decls.append({"name": t.name, "description": desc,
                              "parameters": t.parameters_json_schema})
            # Gemini's functionDeclarations has no ``strict`` field, and its
            # schema subset already rejects most of what strict mode implies.
            # Warn rather than silently ignoring it: a caller that relies on
            # constrained decoding to keep a tool's arguments schema-valid gets
            # unconstrained output instead (mirrors the
            # disable_parallel_tool_use warning below).
            if any(t.strict for t in req.tools if t.builtin is None):
                log.warning("unsupported_tool_strict", provider="gemini")
                self.warn(
                    "tool 'strict' mode has no Gemini equivalent; "
                    "arguments are unconstrained")
            entries: list[dict[str, Any]] = []
            if decls:
                entries.append({"functionDeclarations": decls})
            for t in req.tools:
                if t.builtin is None:
                    continue
                wt = bt.wire_type_for("gemini", t.builtin)
                if wt is None:
                    log.warning("dropping_unhostable_builtin_tool",
                                builtin=t.builtin, provider="gemini")
                    # The CALLER is told too (AUDIT #498): a hosted tool the
                    # caller asked for is gone, and its result will never
                    # arrive.
                    self.warn(
                        f"provider-hosted tool '{t.name}' ({t.builtin}) is not "
                        f"supported by Gemini and was dropped")
                    continue
                entries.append({wt: {}})
            if entries:
                body["tools"] = entries
            # tool_choice maps onto toolConfig.functionCallingConfig. Without
            # it a client asking for {"type":"any"} or a specific tool got
            # prose instead: the choice was dropped entirely, with no log
            # (AUDIT #156).
            fcc = self._function_calling_config(req)
            if fcc:
                body["toolConfig"] = {"functionCallingConfig": fcc}
            # disable_parallel_tool_use has no Gemini equivalent: Gemini emits
            # parallel functionCall parts within one candidate and the API
            # exposes no knob to forbid it. Warn rather than silently ignoring
            # the caller's constraint — a client that serializes tool calls for
            # correctness (file edits, shell state) would otherwise get
            # concurrent calls it did not ask for (AUDIT #156). The warning is
            # also recorded for the caller, not just the log: the constraint
            # came from the request, so the response metadata is where the
            # client can actually see it was dropped.
            if req.gen_params.disable_parallel_tool_use:
                log.warning("unsupported_disable_parallel_tool_use",
                            provider="gemini")
                self.warn(
                    "disable_parallel_tool_use has no Gemini equivalent; "
                    "parallel tool calls cannot be suppressed")
        return body

    @staticmethod
    def _function_calling_config(req: ir.Request) -> dict[str, Any] | None:
        """Gemini ``functionCallingConfig`` for the IR tool choice.

        Mode vocabulary: AUTO (model decides), ANY (must call some function),
        NONE (no calls), and for a named tool ``ANY`` plus
        ``allowedFunctionNames``.
        """
        tc = req.tool_choice
        if tc is None:
            return None
        if isinstance(tc, ir.ToolChoiceNone):
            return {"mode": "NONE"}
        if isinstance(tc, ir.ToolChoiceRequired):
            return {"mode": "ANY"}
        if isinstance(tc, ir.ToolChoiceNamed):
            return {"mode": "ANY", "allowedFunctionNames": [tc.name]}
        if isinstance(tc, ir.ToolChoiceAuto):
            return {"mode": "AUTO"}
        return None

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = orjson.loads(body)
        # Check for prompt-level blocking (no candidates returned)
        pf = as_dict(data.get("promptFeedback"))
        block_reason = pf.get("blockReason")
        # ``or [{}]``/``or {}`` default only a *missing* key: an explicit null
        # or a string candidate raised AttributeError on the next read, which
        # the gateway wraps as a retryable 502 and charges to key health
        # (AUDIT #247).
        _cands = as_list(data.get("candidates"))
        cand = as_dict(_cands[0]) if _cands else {}
        content = as_dict(cand.get("content"))
        turn = ir.AssistantTurn()
        for part in as_list(content.get("parts")):
            if not isinstance(part, dict):
                continue
            if part.get("thought") and "text" in part:
                # Gemini 2.5 thinking models return CoT as a text part flagged
                # ``thought: true``. Emitting it as visible text leaked the
                # model's reasoning to the client (AUDIT #94); route it to
                # ``thinking`` and preserve the signature for replay.
                turn.thinking.append(ir.ThinkingPart(
                    text=part["text"] or "",
                    signature=part.get("thoughtSignature")))
            elif "text" in part:
                # Gemini can hand back a null text part; ToolUsePart/turn.text
                # are str-typed and += None raises TypeError (a 500 on an
                # otherwise valid candidate).
                turn.text += part["text"] or ""
            elif "functionCall" in part:
                fc = as_dict(part["functionCall"])
                # An explicit JSON null name must not reach the IR as a
                # non-str: `.get(k, "")` defaults only a *missing* key, so
                # `{"name": null}` produced ToolUsePart(name=None) and the
                # client received `"name": null` (AUDIT #232).
                fname = as_str(fc.get("name"))
                turn.tool_calls.append(ir.ToolUsePart(
                    id=_synthetic_tool_id(fname),
                    name=fname, args=fc.get("args") or {}))
        if block_reason:
            turn.stop_reason = "content_filter"
        else:
            finish = cand.get("finishReason", "STOP")
            turn.stop_reason = _FINISH_MAP.get(
                finish if isinstance(finish, str) else "", "stop")
            if turn.tool_calls:
                turn.stop_reason = "tool_call"
        u = as_dict(data.get("usageMetadata"))
        turn.usage = ir.Usage(
            prompt_tokens=_token_count(u.get("promptTokenCount")),
            completion_tokens=(_token_count(u.get("candidatesTokenCount"))
                               + _token_count(u.get("thoughtsTokenCount"))),
            cached_tokens=_token_count(u.get("cachedContentTokenCount")),
            reasoning_tokens=_token_count(u.get("thoughtsTokenCount")),
            # Gemini's promptTokenCount totals the prompt INCLUDING the
            # cached content (it is the whole-prompt counter; cachedContent
            # TokenCount is the subset served from cache) — the OpenAI
            # convention, recorded explicitly rather than left to the
            # deployment-wire fallback (the contract ir/types.py states).
            cached_in_prompt=True,
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
            # content (AUDIT #136).
            return []
        # Gemini sends errors as {"error": {"code": ..., "message": ..., "status": ...}}
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message", "unknown gemini error")
            return [dl.StreamError(message=msg, kind="status",
                                   status=err.get("code"))]
        # A prompt-level safety block arrives with no candidate content at all
        # (and often no candidates). The sync decoder maps it to
        # ``content_filter``; the stream decoder never read it, so the pump saw
        # a stream with no finish and called _note_stream_failure — cooling a
        # healthy deployment, penalising a healthy key and billing an estimated
        # partial for a legitimate content filter (AUDIT #76 class). Checked
        # before the candidate guard below so a block is never masked by a
        # malformed candidate riding the same frame.
        pf = payload.get("promptFeedback")
        block_reason = pf.get("blockReason") if isinstance(pf, dict) else None
        # Non-dict candidates/elements carry no semantics; skipping them keeps
        # a malformed frame from raising AttributeError into the pump's generic
        # handler, which cooled a healthy deployment and fed the key's
        # retirement ladder (AUDIT #110 class — the round-49 fix guarded the
        # frame but not its first element). A frame whose first candidate is
        # non-dict has nothing to decode, so it is dropped whole rather than
        # opening the stream with a spurious StreamStart.
        cands = payload.get("candidates")
        if (not block_reason and isinstance(cands, list) and cands
                and not isinstance(cands[0], dict)):
            return []
        # A candidate whose ``content`` is a truthy NON-dict is a corrupt
        # frame. The gate below reads it as ``(cand.get("content") or {}).get
        # ("parts")``, whose ``or {}`` default fires only on a FALSY value, so
        # a number/string/array reached ``.get("parts")`` and raised
        # AttributeError straight out of the decoder — which the pump turns
        # into a StreamError, cooling a healthy deployment for a frame
        # carrying no semantics (AUDIT #386). The sync twin already reads the
        # field through ``as_dict`` (``gemini_adapter.py:308``).
        #
        # Such a frame is dropped WHOLE rather than terminating the stream:
        # the usage-without-finishReason arm below (AUDIT #76) requires a
        # parts-less content as evidence of a clean completion, and a corrupt
        # content is not that evidence. Dropping it lets the next, intact
        # frame carry the turn (the same disposition as the non-dict
        # candidate above).
        if (not block_reason and isinstance(cands, list) and cands
                and isinstance(cands[0], dict)):
            _content = cands[0].get("content")
            if _content and not isinstance(_content, dict):
                return []
        out: list[dl.IRStreamDelta] = []
        if not self._started:
            out.append(dl.StreamStart(model=""))
            self._started = True
            self._saw_function_call = False
            self._tool_seq = 0
            self._saw_tail = False
        if block_reason:
            u = payload.get("usageMetadata")
            if isinstance(u, dict):
                out.append(dl.UsageFinal(
                    prompt=_token_count(u.get("promptTokenCount")),
                    cached=_token_count(u.get("cachedContentTokenCount")),
                    reasoning=_token_count(u.get("thoughtsTokenCount")),
                    output=(_token_count(u.get("candidatesTokenCount"))
                            + _token_count(u.get("thoughtsTokenCount"))),
                    # Same convention as decode_response: promptTokenCount
                    # totals the prompt INCLUDING the cached subset.
                    cached_in_prompt=True))
            out.append(dl.Finish("content_filter"))
            out.append(dl.StreamEnd())
            self._saw_tail = True
            return out
        cand = cands[0] if isinstance(cands, list) and cands else {}
        content = cand.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        parts = parts if isinstance(parts, list) else []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("thought") and "text" in part:
                # Thought parts are reasoning, not the answer: emit them as
                # ThinkingDelta so the client does not render CoT as the reply
                # (AUDIT #94).
                out.append(dl.ThinkingDelta(part["text"] or ""))
            elif "text" in part:
                out.append(dl.TextDelta(part["text"] or ""))
            elif "functionCall" in part:
                fc = part["functionCall"]
                if not isinstance(fc, dict):
                    # Typed-wrong functionCall (null/scalar): skip the part,
                    # not crash on ``fc.get`` (AUDIT #154).
                    continue
                self._saw_function_call = True
                # `index` must stay a per-stream 0-based ordinal (the IR
                # contract keys Open/Args/Close on it); the synthetic *id*
                # comes from the process-global counter so it stays unique
                # across turns (see _synthetic_tool_id).
                n = self._tool_seq
                self._tool_seq += 1
                # A JSON null name must not reach the client (AUDIT #232).
                fname = as_str(fc.get("name"))
                out.append(dl.ToolCallOpen(
                    index=n, id=_synthetic_tool_id(fname), name=fname))
                out.append(dl.ToolCallArgsDelta(index=n,
                                                args_fragment=json.dumps(fc.get("args") or {})))
                out.append(dl.ToolCallClose(index=n))
        u = payload.get("usageMetadata")
        if not isinstance(u, dict):
            # A truthy non-dict usageMetadata must not reach u.get (AUDIT
            # #154); treat it as absent.
            u = None
        finish = cand.get("finishReason")
        if finish and not self._saw_tail:
            if u:
                out.append(dl.UsageFinal(
                    prompt=_token_count(u.get("promptTokenCount")),
                    cached=_token_count(u.get("cachedContentTokenCount")),
                    reasoning=_token_count(u.get("thoughtsTokenCount")),
                    output=(_token_count(u.get("candidatesTokenCount"))
                            + _token_count(u.get("thoughtsTokenCount"))),
                    cached_in_prompt=True))
            # A real function call in this response wins over the mapped finish
            # reason — never sniff the serialized candidate text for "tool".
            if self._saw_function_call:
                out.append(dl.Finish("tool_call"))
            else:
                out.append(dl.Finish(_FINISH_MAP.get(
                    finish if isinstance(finish, str) else "", "stop")))
            out.append(dl.StreamEnd())
            self._saw_tail = True
        elif u and not self._saw_tail and not as_dict(cand.get("content")).get("parts"):
            # Usage without finishReason: emitted on some SAFETY-truncated and
            # mid-stream-cut responses. Treat it as a clean completion — the
            # pump otherwise reports `upstream stream ended without completion`
            # and cools a healthy deployment / penalises a healthy key
            # (AUDIT #76).
            #
            # But usage rides on *intermediate* chunks too (Gemini 2.5 and
            # Vertex attach usageMetadata to every chunk). Terminating on any
            # usage-bearing frame ended the stream at the first chunk, so the
            # consumer broke on StreamEnd and the rest of the answer was
            # silently truncated at HTTP 200 (AUDIT #134). A genuine terminal
            # frame carries no content parts, so require that as well.
            #
            # ``_saw_tail`` makes the tail exactly-once across BOTH arms: this
            # one fires on a parts-less usage-bearing intermediate frame, and
            # the ``finish`` frame that follows would otherwise emit a second
            # UsageFinal/Finish/StreamEnd — every consumer keeps the last
            # value, so the stream was billed on the intermediate frame's
            # counts and the client saw a terminal frame mid-stream
            # (AUDIT #161).
            out.append(dl.UsageFinal(
                prompt=_token_count(u.get("promptTokenCount")),
                cached=_token_count(u.get("cachedContentTokenCount")),
                reasoning=_token_count(u.get("thoughtsTokenCount")),
                output=(_token_count(u.get("candidatesTokenCount"))
                        + _token_count(u.get("thoughtsTokenCount"))),
                cached_in_prompt=True))
            if self._saw_function_call:
                out.append(dl.Finish("tool_call"))
            else:
                out.append(dl.Finish("stop"))
            out.append(dl.StreamEnd())
            self._saw_tail = True
        return out

