"""NVIDIA NIM adapter: OpenAI-compatible chat completions with NIM-specific logic.

NIM (``integrate.api.nvidia.com``) speaks the OpenAI Chat Completions wire
format but differs from a plain ``openai-compatible`` endpoint in three ways
that require a dedicated adapter:

1. **Reasoning via ``chat_template_kwargs``** — NIM is vLLM-backed.  Instead of
   OpenAI's ``reasoning_effort`` field, reasoning is controlled through
   ``extra_body.chat_template_kwargs`` with ``thinking`` (bool),
   ``enable_thinking`` (bool), and ``reasoning_budget`` (int) keys.  The adapter
   translates the IR's ``reasoning_effort`` / ``thinking_budget`` into this
   format and strips the OpenAI-native ``reasoning_effort`` field.

2. **Tool schema sanitization** — NIM rejects boolean JSON Schema subschemas
   (``"additionalProperties": true``) and unsafe parameter names (``"type"``)
   that collide with JSON Schema keywords.  The adapter sanitizes tool
   definitions before sending them upstream and un-aliases the arguments on
   the way back.

3. **Native MiniMax tool stream** — some NIM-hosted models (MiniMax-M3,
   StepFun) leak tool calls as text content using a custom XML-like namespace
   (``]<]minimax[>[``) instead of proper OpenAI ``tool_calls`` deltas.  The
   adapter's ``decode_stream_event`` runs a stateful framer that detects this
   markup, buffers it, parses it into structured tool calls, and emits proper
   ``ToolCallOpen`` / ``ToolCallArgsDelta`` / ``ToolCallClose`` deltas.

For non-streaming responses, the same markup may appear in the ``content``
field; ``decode_response`` detects and converts it to ``tool_calls``.

See: https://docs.nvidia.com/nim/large-language-models/latest/
"""

from __future__ import annotations

import json
from typing import Any

import orjson

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.nim_native_tools import (
    MiniMaxFramer,
    NimToolProtocolError,
    native_calls_to_deltas,
    parse_tool_block,
    tool_schemas_from_body,
)
from wiwi.providers.nim_tool_schema import (
    collect_nim_tool_aliases,
    sanitize_nim_tool_schemas,
    unalias_nim_tool_args,
)
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl


class NimAdapter(OpenAIAdapter):
    """NVIDIA NIM: extends OpenAI adapter with NIM-specific translations."""

    provider_type = "nvidia-nim"

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        return {"Authorization": f"Bearer {key.secret}"}

    def build_url(self, base_url: str, model_id: str, stream: bool, kind: str) -> str:
        base = base_url.rstrip("/")
        if kind == "embeddings":
            return f"{base}/embeddings"
        return f"{base}/chat/completions"

    # -- encode: reasoning -> chat_template_kwargs + tool schema sanitization ---

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        # Delegate the bulk of encoding to the OpenAI adapter.  The base
        # adapter will forward reasoning_effort (NIM is not in the
        # is_native_openai exclusion set... actually it IS now), so we strip
        # it below and translate to chat_template_kwargs instead.
        body = super().encode_request(req, model_id, deployment_params)

        # NIM does not accept the OpenAI-native reasoning_effort field.
        body.pop("reasoning_effort", None)

        # Translate reasoning config to NIM's chat_template_kwargs format.
        g = req.gen_params
        extra_body: dict[str, Any] = body.get("extra_body", {})
        if not isinstance(extra_body, dict):
            extra_body = {}

        if g.reasoning_effort == "none":
            # Explicitly disable thinking.
            ctk = extra_body.setdefault("chat_template_kwargs", {})
            ctk["thinking"] = False
            ctk["enable_thinking"] = False
        elif g.reasoning_effort:
            # Named effort level -> enable thinking with mapped budget.
            ctk = extra_body.setdefault("chat_template_kwargs", {})
            ctk["thinking"] = True
            ctk["enable_thinking"] = True
            budget = ir.effort_to_thinking_budget(g.reasoning_effort)
            if budget is not None:
                ctk["reasoning_budget"] = budget
        elif g.thinking_budget is not None:
            # Direct token budget -> enable thinking with that budget.
            ctk = extra_body.setdefault("chat_template_kwargs", {})
            ctk["thinking"] = True
            ctk["enable_thinking"] = True
            ctk["reasoning_budget"] = g.thinking_budget

        if extra_body:
            body["extra_body"] = extra_body

        # Sanitize tool schemas: strip boolean subschemas, alias unsafe params.
        if body.get("tools"):
            body["tools"] = sanitize_nim_tool_schemas(body["tools"])

        return body

    # -- decode (non-streaming): handle native tool markup in content -----------

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        turn = super().decode_response(status, body)

        # If the model returned native MiniMax tool markup in the content
        # field instead of structured tool_calls, detect and convert it.
        # This is rare for non-streaming (NIM usually returns proper
        # tool_calls), but handle it for robustness.
        if not turn.tool_calls and turn.text:
            hit = _detect_native_markup(turn.text)
            if hit is not None:
                block_start, block_end, markup = hit
                # We don't have the original request's tool schemas here,
                # so parse without schema validation (best-effort).
                try:
                    calls = parse_tool_block(markup, {}, self._tool_aliases)
                except NimToolProtocolError:
                    calls = ()
                if calls:
                    # Remove only the markup block; keep surrounding text.
                    turn.text = (turn.text[:block_start]
                                 + turn.text[block_end:]).strip()
                    for c in calls:
                        turn.tool_calls.append(ir.ToolUsePart(
                            id=f"call_nim_resp_{c.index}",
                            name=c.name, args=c.arguments,
                            raw_args=json.dumps(c.arguments)))

        # Un-alias structured tool_calls args (e.g. a param named "type"
        # sent upstream as "_nim_arg_type") when the request used aliases.
        self._unalias_turn_tool_calls(turn)

        return turn

    # -- decode (streaming): framer + native tool normalization ------------------

    def __init__(self) -> None:
        super().__init__()
        # Per-request framer state for native MiniMax tool normalization.
        self._content_framer = MiniMaxFramer()
        self._reasoning_framer = MiniMaxFramer()
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._tool_aliases: dict[str, dict[str, str]] = {}
        # Args fragments for tools with aliased params are buffered per
        # index and emitted once (un-aliased) when the call closes.
        self._buffered_args: dict[int, str] = {}

    def set_tool_context(self, body: dict[str, Any]) -> None:
        """Capture tool schemas and aliases from the encoded request body.

        Called by the gateway after encode_request so decode_stream_event
        can parse native tool markup and un-alias arguments.  The gateway
        passes the encoded body via ``ctx.metadata``.
        """
        self._tool_schemas = tool_schemas_from_body(body)
        if body.get("tools"):
            self._tool_aliases = collect_nim_tool_aliases(body["tools"])

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        if data == "[DONE]":
            out = self._flush_framers()
            out.append(dl.StreamEnd())
            return out
        try:
            chunk = orjson.loads(data)
        except json.JSONDecodeError:
            return []

        out: list[dl.IRStreamDelta] = []

        # Usage may ride in any chunk.
        u = chunk.get("usage")
        if u:
            dp = u.get("prompt_tokens_details") or {}
            dc = u.get("completion_tokens_details") or {}
            out.append(dl.UsageFinal(
                prompt=u.get("prompt_tokens", 0), cached=dp.get("cached_tokens", 0),
                reasoning=dc.get("reasoning_tokens", 0),
                output=u.get("completion_tokens", 0)))

        choices = chunk.get("choices") or []
        if not choices:
            return out
        c = choices[0]
        delta = c.get("delta") or {}

        # Feed content through the MiniMax framer.  The framer separates
        # visible text from native tool markup.  If a complete tool block
        # is detected, emit tool-call deltas instead of text.
        content = delta.get("content")
        if content:
            visible = self._feed_safely(self._content_framer, content, "content")
            if visible:
                out.append(dl.TextDelta(visible))
            if self._content_framer.tool_block is not None:
                out.extend(self._emit_native_calls(self._content_framer.tool_block))
                self._content_framer.tool_block = None

        # Reasoning content also goes through a framer (some models leak
        # tool markup in reasoning too).
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            visible_r = self._feed_safely(self._reasoning_framer, reasoning,
                                          "reasoning")
            if visible_r:
                out.append(dl.ThinkingDelta(visible_r))
            if self._reasoning_framer.tool_block is not None:
                out.extend(self._emit_native_calls(self._reasoning_framer.tool_block))
                self._reasoning_framer.tool_block = None

        # Structured tool_calls (normal OpenAI format) -- pass through.
        tool_calls = delta.get("tool_calls") or []
        for i, tc in enumerate(tool_calls):
            idx = tc.get("index", i)
            fn = tc.get("function") or {}
            name_fragment = fn.get("name", "")
            if tc.get("id"):
                if idx in self._open_tool_indices:
                    self._flush_buffered_args(idx, out)
                    out.append(dl.ToolCallClose(index=idx))
                self._open_tool_indices.add(idx)
                self._tool_names[idx] = name_fragment or ""
                out.append(dl.ToolCallOpen(index=idx, id=tc["id"],
                                           name=self._tool_names[idx]))
            elif name_fragment and idx in self._open_tool_indices:
                self._tool_names[idx] = self._tool_names.get(idx, "") + name_fragment
            if fn.get("arguments"):
                if self._tool_aliases.get(self._tool_names.get(idx, "")):
                    # Aliased tool: buffer fragments so the completed JSON
                    # can be un-aliased before the client sees it.
                    self._buffered_args[idx] = (
                        self._buffered_args.get(idx, "") + fn["arguments"])
                else:
                    out.append(dl.ToolCallArgsDelta(index=idx,
                                                    args_fragment=fn["arguments"]))

        fr = c.get("finish_reason")
        if fr:
            # Flush framers: any remaining text or completed tool block.
            flushed = self._flush_framers()
            out.extend(flushed)
            # Close ALL still-open tool calls (parallel tools).
            for open_idx in sorted(self._open_tool_indices):
                self._flush_buffered_args(open_idx, out)
                out.append(dl.ToolCallClose(index=open_idx))
            self._open_tool_indices.clear()
            self._tool_names.clear()
            self._buffered_args.clear()
            out.append(dl.Finish({"stop": "stop", "length": "length",
                                 "tool_calls": "tool_call",
                                 "content_filter": "content_filter"}.get(fr, "stop")))
        return out

    def _feed_safely(self, framer: MiniMaxFramer, chunk: str,
                     which: str) -> str:
        """Feed a framer chunk; on a protocol error degrade to plain text.

        The framer raises ``NimToolProtocolError`` for content that is
        benign model output (a namespace-like string in plain text, text
        after a completed tool block, an oversized block).  Rather than
        aborting the whole stream, reset the framer and pass the chunk
        through as visible text.
        """
        try:
            return framer.feed(chunk)
        except NimToolProtocolError:
            fresh = MiniMaxFramer()
            if which == "content":
                self._content_framer = fresh
            else:
                self._reasoning_framer = fresh
            return chunk

    def _flush_buffered_args(self, idx: int, out: list[dl.IRStreamDelta]) -> None:
        """Emit buffered args for an aliased tool call, un-aliased."""
        buf = self._buffered_args.pop(idx, None)
        if buf is None:
            return
        aliases = self._tool_aliases.get(self._tool_names.get(idx, ""), {})
        fragment = buf
        try:
            args = orjson.loads(buf)
            if isinstance(args, dict):
                fragment = json.dumps(unalias_nim_tool_args(args, aliases),
                                      ensure_ascii=False, separators=(",", ":"))
        except (ValueError, json.JSONDecodeError):
            pass  # leave the raw fragment as-is
        out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=fragment))

    def _unalias_turn_tool_calls(self, turn: ir.AssistantTurn) -> None:
        """Un-alias structured tool_calls args on a non-streaming turn."""
        if not self._tool_aliases:
            return
        for tc in turn.tool_calls:
            aliases = self._tool_aliases.get(tc.name)
            if aliases and isinstance(tc.args, dict):
                tc.args = unalias_nim_tool_args(tc.args, aliases)
                tc.raw_args = json.dumps(tc.args, ensure_ascii=False,
                                         separators=(",", ":"))

    def _flush_framers(self) -> list[dl.IRStreamDelta]:
        """Flush pending text from both framers and emit any complete tool block."""
        out: list[dl.IRStreamDelta] = []
        try:
            tail = self._content_framer.finish()
        except NimToolProtocolError:
            tail = ""
            self._content_framer = MiniMaxFramer()
        if tail:
            out.append(dl.TextDelta(tail))
        if self._content_framer.tool_block is not None:
            out.extend(self._emit_native_calls(self._content_framer.tool_block))
            self._content_framer.tool_block = None

        try:
            tail_r = self._reasoning_framer.finish()
        except NimToolProtocolError:
            tail_r = ""
            self._reasoning_framer = MiniMaxFramer()
        if tail_r:
            out.append(dl.ThinkingDelta(tail_r))
        if self._reasoning_framer.tool_block is not None:
            out.extend(self._emit_native_calls(self._reasoning_framer.tool_block))
            self._reasoning_framer.tool_block = None
        return out

    def _emit_native_calls(self, block: str) -> list[dl.IRStreamDelta]:
        """Parse a complete native tool block and emit IRStreamDelta tool calls."""
        try:
            calls = parse_tool_block(block, self._tool_schemas, self._tool_aliases)
        except NimToolProtocolError:
            # On malformed markup, emit the raw block as text so the client
            # sees something rather than a silent drop.
            return [dl.TextDelta(block)]
        return native_calls_to_deltas(calls, self._open_tool_indices, self._tool_names)


# -- helpers -------------------------------------------------------------------

def _detect_native_markup(text: str) -> tuple[int, int, str] | None:
    """Locate a complete native tool block in ``text``.

    Returns ``(block_start, block_end, inner)`` — the span of the whole
    block (markers included) and its inner markup — or ``None`` when no
    complete block is found.
    """
    from wiwi.providers.nim_native_tools import _TOOL_BLOCK_END, _TOOL_BLOCK_START
    start = text.find(_TOOL_BLOCK_START)
    if start < 0:
        return None
    end = text.find(_TOOL_BLOCK_END, start + len(_TOOL_BLOCK_START))
    if end < 0:
        return None
    inner = text[start + len(_TOOL_BLOCK_START):end]
    return start, end + len(_TOOL_BLOCK_END), inner
