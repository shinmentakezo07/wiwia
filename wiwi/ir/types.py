"""Canonical Internal Representation (IR) — the single source of truth for all translation.

Every wire dialect (OpenAI Chat, OpenAI Responses, Anthropic Messages) decodes into
these types; every provider adapter encodes from them. See docs/CORE.md and
docs/ARCHITECTURE.md §4.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
# Anthropic's stop_reason vocabulary is wider than the other dialects'. Three of
# its values carry meaning that "stop" cannot: ``pause_turn`` tells the client a
# provider-hosted tool loop is mid-flight and the turn must be re-sent to
# continue; ``stop_sequence`` reports which caller-supplied sequence fired; and
# ``model_context_window_exceeded`` distinguishes a context overflow from a
# normal completion. Collapsing all of them into "stop" made Claude Code end a
# server-tool turn early and made auto-compact unable to see an overflow.
# Encoders for the narrower dialects map these onto their own vocabulary.
StopReason = Literal[
    "stop", "length", "tool_call", "content_filter",
    "pause_turn", "stop_sequence", "context_window_exceeded", "compaction",
]

CacheControl = dict[str, Any] | None  # e.g. {"type": "ephemeral"} (Anthropic passthrough, G8)

# Closed vocabularies for the block types whose exact spelling is a REPLAY
# requirement. Anthropic rejects a history whose result block does not match
# the spelling of the call it answers, so these were documented sets carried in
# a bare ``str``: a typo or a junk upstream value propagated verbatim to the
# wire and produced a 400 naming no field the caller wrote. Typed as ``Literal``
# so a wrong value is caught statically, and normalized in ``__post_init__`` so
# one that slips in at runtime (the decoders read upstream strings) cannot
# reach an encoder.
ToolUseBlockType = Literal["tool_use", "server_tool_use", "mcp_tool_use"]
ThinkingBlockType = Literal["thinking", "redacted_thinking"]

_TOOL_USE_BLOCK_TYPES = frozenset({"tool_use", "server_tool_use", "mcp_tool_use"})
_THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})


@dataclass
class TextPart:
    text: str
    cache_control: CacheControl = None


@dataclass
class ImagePart:
    url: str | None = None
    b64: str | None = None
    mime: str = "image/png"
    file_id: str | None = None  # Anthropic Files API reference (source.type=file)
    # Prompt-cache breakpoint. Claude Code marks long-lived image prefixes (a
    # screenshot reused across turns) exactly as it marks text; without this
    # field the marker was dropped on decode and the prefix re-billed at 1x
    # every turn, because Anthropic only caches at a breakpoint.
    cache_control: CacheControl = None


@dataclass
class ToolUsePart:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw_args: str | None = None  # original JSON string if provider gave one
    # Canonical builtin name (ir/builtin_tools.py) when the PROVIDER executed
    # this call itself (Anthropic ``server_tool_use``: web_search,
    # code_execution, an MCP tool). Encoders must suppress these — the client
    # never dispatched them and cannot return a result — and suppression has to
    # key on this flag, not on the tool's *name*: a caller may legitimately
    # define a function tool called ``web_search``, and a name match deleted
    # that real call from the response.
    builtin: str | None = None
    # Original Anthropic block type when the inbound dialect distinguishes
    # them: ``tool_use`` (client-dispatched), ``server_tool_use``
    # (provider-hosted, e.g. web_search) or ``mcp_tool_use`` (executed by a
    # server listed in ``mcp_servers``). Replay must re-emit the same type: a
    # ``mcp_tool_result`` whose paired call was rewritten to a plain
    # ``tool_use`` is an unpaired result block and Anthropic rejects the
    # history. Mirrors ToolResultPart.block_type.
    block_type: ToolUseBlockType = "tool_use"
    # Prompt-cache breakpoint on the CALL block. Anthropic accepts a
    # ``cache_control`` marker on a ``tool_use`` block exactly as it does on
    # text, image and document blocks, and (per the tool-use caching docs) on
    # any member of a batch action. Without this field the marker was dropped
    # on decode and the replayed prefix lost its breakpoint — the same defect
    # ``ImagePart.cache_control`` documents, where losing the marker re-billed
    # a long stable prefix at 1x on every turn.
    cache_control: CacheControl = None

    def __post_init__(self) -> None:
        # Providers/decoders can hand us a non-string id (OpenAI-compatible
        # gateways sometimes emit numeric ids). Wire dialects and adapters
        # must emit a string id, so coerce once at the IR boundary.
        if not isinstance(self.id, str):
            self.id = str(self.id)
        # An unknown block type has no legal wire spelling: the only consumer
        # (the Anthropic encoder) would emit it verbatim and the API would
        # reject the history. Degrade to the client-dispatched default, which
        # is always a legal spelling, instead of forwarding junk.
        if not isinstance(self.block_type, str) or self.block_type not in _TOOL_USE_BLOCK_TYPES:
            self.block_type = "tool_use"


@dataclass
class ToolResultPart:
    tool_use_id: str
    content: str
    is_error: bool = False
    cache_control: CacheControl = None
    # Original result-block type when the inbound dialect distinguishes them
    # (Anthropic's server-tool family: web_search_tool_result, ...). Replay
    # must re-emit the same block type or Anthropic rejects the history.
    #
    # Deliberately ``str`` rather than a ``Literal``: the family is OPEN — the
    # decoder accepts any ``*_tool_result`` spelling so a new server tool
    # version replays without an IR change — so there is no closed set to
    # enumerate, and a Literal here would silently reject the next one.
    block_type: str = "tool_result"
    # Multimodal tool results (e.g. screenshots from computer-use tools).
    # Adapters with native image support re-emit them as block-form content;
    # others drop them (text-only providers).
    images: list[ImagePart] = field(default_factory=list)
    # Non-text, non-image content blocks carried verbatim from the inbound
    # dialect. Anthropic's tool-search feature returns discovered tools as
    # ``tool_reference`` blocks INSIDE a tool_result; the model only learns
    # which deferred tool to load from those blocks, so flattening the result
    # to its text made every discovered tool invisible. Only the Anthropic
    # encoder re-emits them (other dialects have no equivalent block); a
    # provider that cannot carry them still receives the flattened ``content``.
    extra_blocks: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.tool_use_id, str):
            self.tool_use_id = str(self.tool_use_id)


@dataclass
class ThinkingPart:
    text: str
    signature: str | None = None
    # Anthropic redacted_thinking: the block is an opaque encrypted blob in
    # ``data``; replay must re-emit the same block type or Anthropic rejects
    # the history (mirror of ToolResultPart.block_type). Other adapters see
    # an empty-text thinking part and drop it harmlessly.
    block_type: ThinkingBlockType = "thinking"
    data: str | None = None
    # OpenAI Responses ``reasoning.encrypted_content``: the opaque blob the
    # Responses API hands back and demands back on the next turn.
    #
    # Codex sends ``include: ["reasoning.encrypted_content"]`` on EVERY
    # request and must replay every output item verbatim, blob included.
    # Without a carrier here the blob was dropped at decode and the next
    # turn's history was rejected with "'encrypted_content' is required" —
    # so reasoning-model multi-turn through /v1/responses broke on turn 2
    # (AUDIT #355). The field is Responses-shaped; other dialects ignore it.
    encrypted_content: str | None = None

    def __post_init__(self) -> None:
        # Same rule as ToolUsePart: an unknown spelling cannot be replayed, so
        # normalize to the always-legal default rather than emit it upstream.
        if not isinstance(self.block_type, str) or self.block_type not in _THINKING_BLOCK_TYPES:
            self.block_type = "thinking"


# Reserved multimodal kinds (field names stable now, translation later).
@dataclass
class AudioPart:
    b64: str | None = None
    mime: str = "audio/wav"


@dataclass
class DocumentPart:
    b64: str | None = None
    url: str | None = None
    mime: str = "application/pdf"
    name: str | None = None  # Anthropic block-level "title"
    context: str | None = None  # Anthropic block-level "context"
    cache_control: CacheControl = None  # prompt-cache breakpoint (see ImagePart)


Part = (
    TextPart | ImagePart | ToolUsePart | ToolResultPart | ThinkingPart
    | AudioPart | DocumentPart
)


@dataclass
class Message:
    role: Role
    parts: list[Part] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str = ""
    parameters_json_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    strict: bool | None = None  # G3: OpenAI structured-output strictness / Anthropic strict tool use
    input_examples: list[dict[str, Any]] | None = None  # Anthropic tool-use examples
    cache_control: CacheControl = None  # Anthropic prompt-cache breakpoint on tool def
    # Provider-hosted builtin tool (e.g. web_search): canonical name from
    # ir/builtin_tools.py, not a function tool the model defines. builtin_config
    # carries the provider-common subset (max_uses, allowed_domains, ...);
    # an unknown builtin keeps its raw wire type under "_wire_type".
    builtin: str | None = None
    builtin_config: dict[str, Any] | None = None
    # Anthropic tool search: the tool's definition is sent on every request but
    # held out of the model's context until a search discovers it. The API
    # needs it server-side to expand ``tool_reference`` blocks, so dropping the
    # flag does not just lose a hint — it loads every deferred tool up front,
    # which is what degrades tool-selection accuracy on large catalogs.
    defer_loading: bool | None = None


@dataclass
class ToolChoiceAuto:
    pass


@dataclass
class ToolChoiceNone:
    pass


@dataclass
class ToolChoiceRequired:
    pass


@dataclass
class ToolChoiceNamed:
    name: str


ToolChoice = ToolChoiceAuto | ToolChoiceNone | ToolChoiceRequired | ToolChoiceNamed


@dataclass
class ResponseFormat:
    type: Literal["text", "json_object", "json_schema"] = "text"
    json_schema: dict[str, Any] | None = None
    name: str | None = None  # schema name for OpenAI
    strict: bool | None = None


@dataclass
class GenParams:
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] = field(default_factory=list)
    seed: int | None = None
    n: int = 1  # G3: >1 rejected on backends without native support
    response_format: ResponseFormat | None = None
    parallel_tool_calls: bool | None = None
    # Anthropic: disable_parallel_tool_use rides inside tool_choice, but the IR
    # keeps it separate so either dialect can set it independently.
    disable_parallel_tool_use: bool | None = None
    reasoning_effort: str | None = None  # "none" | "minimal" | "low" … "max"
    thinking_budget: int | None = None
    # Anthropic 2026 ``output_config.effort`` — the knob behind Claude Code's
    # /effort command, --effort flag, CLAUDE_CODE_EFFORT_LEVEL and per-skill
    # frontmatter. Distinct from reasoning_effort: it is Anthropic's own
    # top-level field, so an Anthropic upstream receives it verbatim rather
    # than through the effort→budget mapping, and other providers fall back to
    # reasoning_effort when this is set.
    effort: str | None = None
    # Anthropic thinking modes: "enabled" (budget via thinking_budget), "adaptive"
    # (model-driven budget, no budget_tokens), "disabled" (also sets
    # reasoning_effort="none" so OpenAI upstreams disable reasoning).
    thinking_type: str | None = None  # "enabled" | "adaptive" | "disabled"
    top_k: int | None = None  # Anthropic (and Gemini) sampling param; ignored by OpenAI
    metadata: dict[str, Any] = field(default_factory=dict)

    def effective_reasoning_effort(self) -> str | None:
        """Return reasoning_effort, deriving it from thinking_budget if not set.

        ``effort`` (Anthropic's ``output_config.effort``) participates as a
        fallback so a Claude Code session that sets only the effort slider
        still produces a reasoning control on a non-Anthropic backend, which
        has no ``output_config`` of its own.
        """
        if self.reasoning_effort:
            return self.reasoning_effort
        # ``effort`` is an EXPLICIT caller selection — /effort, --effort,
        # CLAUDE_CODE_EFFORT_LEVEL, per-skill frontmatter — so it outranks the
        # budget-derived guess. Claude Code sends ``thinking.budget_tokens``
        # and ``output_config.effort`` together on most turns; letting the
        # budget win silently rewrote every non-Anthropic backend's reasoning
        # level to whatever the budget happened to round to (8000 -> "medium"),
        # so /effort was a no-op everywhere except a native Anthropic upstream.
        if self.effort:
            return self.effort
        if self.thinking_budget is not None:
            return thinking_budget_to_effort(self.thinking_budget)
        return None

    def effective_thinking_budget(self) -> int | None:
        """Return thinking_budget, deriving it from reasoning_effort if not set.

        Returns None when reasoning_effort is 'none' (thinking disabled) or
        when neither field is set, so adapters can distinguish 'no thinking'
        from 'use a default budget.'

        Returns None for an UNUSABLE budget (a non-numeric value, or a ``bool``)
        rather than forwarding it: this is typed ``int | None`` but
        ``GenParams`` is a plain dataclass with no runtime enforcement, and
        every caller does arithmetic on the result (``max(budget, 1024)`` in the
        Anthropic and OpenRouter adapters, ``thinkingBudget`` in Gemini), so a
        raw non-numeric raised ``TypeError`` out of ``encode_request`` (AUDIT
        #388). None is the value the callers already handle as "no resolvable
        budget — leave thinking OFF", so a malformed budget degrades the same
        way an unknown effort does, instead of 500ing. A float is preserved:
        the callers only compare and add, so ``8000.0`` has always been usable.
        """
        budget = coerce_int(self.thinking_budget)
        if budget is not None:
            return budget
        if self.reasoning_effort:
            return effort_to_thinking_budget(self.reasoning_effort)
        if self.effort:
            return effort_to_thinking_budget(self.effort)
        return None


# Reasoning effort ↔ thinking budget mapping.
# Approximate token budgets per effort level. These are conservative defaults;
# providers that natively support effort levels (OpenAI) use the string directly,
# while providers that use token budgets (Anthropic) get the mapped value.
# "none" means disable thinking entirely (supported by GPT-5.x reasoning models).
# "minimal" (2026) shares the 1024 floor with "low"; "max" (2026) shares the
# 64000 cap with "xhigh".
_EFFORT_BUDGETS: dict[str, int | None] = {
    "none": None,
    "minimal": 1024,
    "low": 1024,
    "medium": 8000,
    "high": 32000,
    "xhigh": 64000,
    "max": 64000,
}


def effort_to_thinking_budget(effort: str) -> int | None:
    """Map a reasoning_effort level to a thinking token budget.

    Returns None for 'none' (thinking disabled) and for UNKNOWN values: a
    typo ('hight') or a future level this map has not learned must leave
    thinking OFF, never silently enable it at an arbitrary budget. Callers
    must check the return before setting a thinking config.

    Total by construction: a non-``str`` effort (a list or dict forwarded
    verbatim by the Responses decoder) is unhashable, so the ``.get`` below
    would raise ``TypeError`` inside an adapter's encode path and 500 the
    request on every Anthropic/Gemini deployment (AUDIT #351). The type is
    not enforced at the IR boundary — ``GenParams`` is a plain dataclass and
    the inbound decoders forward whatever the client sent — so the guard
    belongs here, which closes the class for every present and future caller
    rather than one codec at a time.
    """
    if not isinstance(effort, str):
        return None
    return _EFFORT_BUDGETS.get(effort)


def coerce_int(value: Any) -> int | None:
    """Coerce a JSON numeric to ``int``, or return None when it is not one.

    Wire decoders must not forward a malformed ``max_tokens`` (object, list,
    string, bool) upstream verbatim: the provider would reject it with a
    confusing error far from its origin, or silently ignore it (AUDIT #83).
    A genuine ``bool`` is rejected even though ``isinstance(True, int)`` is
    True — ``max_tokens=true`` is a client bug, not "1".
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def thinking_budget_to_effort(budget: int) -> str:
    """Map a thinking token budget to the nearest reasoning_effort level.

    A budget of 0 means "thinking disabled" and maps to "none" — the inverse of
    ``effort_to_thinking_budget("none") is None``. Previously 0 fell through to
    "low", which turned thinking ON for a caller that had disabled it.

    Total by construction, mirroring ``effort_to_thinking_budget`` above (AUDIT
    #351/#388): a non-numeric budget must not reach the ``<=`` ladder, which
    raises ``TypeError``. ``thinking_budget`` is typed ``int | None`` but
    ``GenParams`` is a plain dataclass and callers may set it uncoerced, and the
    one accessor that calls this (``effective_reasoning_effort``) runs inside six
    adapters' ``encode_request`` — so the guard belongs here, closing the class
    for every present and future caller rather than one adapter at a time. An
    unusable value degrades to "none" (thinking off), matching the inverse's
    rule that an unknown effort leaves thinking OFF rather than enabling it at
    an arbitrary budget. A ``bool`` is rejected even though
    ``isinstance(True, int)`` is True — ``thinking_budget=true`` is a client
    bug, not "1", the same rule ``coerce_int`` documents. A float is accepted:
    the ladder only compares, so ``8000.0`` was always well-typed here.

    Note: "minimal" and "max" are intentionally NOT reachable from here. They
    alias "low" (1024) and "xhigh" (64000) in the forward map, so the inverse
    keeps its original boundaries for those collisions.
    """
    if isinstance(budget, bool) or not isinstance(budget, (int, float)):
        return "none"
    if budget <= 0:
        return "none"
    if budget <= 2048:
        return "low"
    if budget <= 16000:
        return "medium"
    if budget <= 48000:
        return "high"
    return "xhigh"


@dataclass
class Request:
    model: str
    messages: list[Message]
    tools: list[Tool] = field(default_factory=list)
    tool_choice: ToolChoice | None = None
    gen_params: GenParams = field(default_factory=GenParams)
    stream: bool = False
    stream_options_include_usage: bool = False  # G4: only when client asks
    # Raw dialect-specific extras that codecs chose not to map but must not lose.
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    # True when these counts did NOT come from the provider and were estimated
    # locally (the streaming fallback when upstream omitted usage). Replaces
    # ``reasoning_estimated``, which the fallback set from a *whole-usage*
    # estimate and so mislabelled a prompt estimate as a reasoning one
    # (AUDIT #131).
    estimated: bool = False
    # Anthropic cache bookkeeping (G8-adjacent accounting)
    cache_creation_tokens: int = 0
    # Whether ``prompt_tokens`` ALREADY CONTAINS ``cached_tokens``. Recorded by
    # the adapter that decoded the counts, because it is a property of the
    # wire's usage object, not of the provider: OpenAI totals the prompt
    # (cached is a subset), Anthropic's ``input_tokens`` excludes cache reads.
    #
    # ``None`` means "the decoder did not say" and the gateway falls back to
    # ``prompt_includes_cached(dep)``, which infers the convention from the
    # deployment's wire shape. That inference has already caused two billing
    # defects (AUDIT #9 and #227 — the second because an opencode deployment
    # serving claude-* speaks the Messages wire, so a provider-type test picked
    # the wrong branch and billed fresh input at $0). A decoder that knows the
    # answer must record it rather than let the guess run.
    cached_in_prompt: bool | None = None


@dataclass
class AssistantTurn:
    """The model's output as IR: text + thinking + tool calls, plus outcome."""

    text: str = ""
    thinking: list[ThinkingPart] = field(default_factory=list)
    tool_calls: list[ToolUsePart] = field(default_factory=list)
    stop_reason: StopReason = "stop"
    stop_sequence: str | None = None  # matched stop sequence (Anthropic surfaces it)
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] | None = None  # provider-native response for passthrough extras
    # Blocks a provider-executed tool produced, in emission order
    # (``web_search_tool_result``, ``tool_search_tool_result``, ...). The
    # Anthropic encoder re-emits them after the calls they answer — without
    # them a replayed server-tool turn loses the search hits and the
    # ``tool_reference`` entries tool search discovered, and an unpaired
    # ``server_tool_use`` is rejected by the API on the next turn.
    server_blocks: list[dict[str, Any]] = field(default_factory=list)
    # Per-token log probabilities, in the OpenAI ``choices[0].logprobs`` shape
    # (``{"content": [{"token", "logprob", "bytes", "top_logprobs"}], ...}``).
    #
    # The carrier for AUDIT #326. The client sends ``{"logprobs": true,
    # "top_logprobs": 5}``; the request rides ``Request.extras`` untyped, is
    # forwarded upstream, and the tokens are BILLED as output — but no
    # adapter decoded ``choices[0].logprobs``, no IR field held it, and no
    # encoder emitted it, so the client silently received no logprobs for
    # money it had already paid.
    #
    # Kept as the provider's own dict rather than a modelled dataclass
    # because the shape IS the OpenAI wire shape, and a second dialect would
    # have to be translated into it before the caller could read it anyway.
    # ``None`` means "the upstream returned no logprobs", which is
    # different from "an empty list" — the encoders emit the key only when
    # this is not None, so a response never grows a phantom field.
    logprobs: dict[str, Any] | None = None
