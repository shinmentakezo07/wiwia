"""Canonical Internal Representation (IR) — the single source of truth for all translation.

Every wire dialect (OpenAI Chat, OpenAI Responses, Anthropic Messages) decodes into
these types; every provider adapter encodes from them. See docs/CORE.md and
docs/ARCHITECTURE.md §4.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
StopReason = Literal["stop", "length", "tool_call", "content_filter"]

CacheControl = dict[str, Any] | None  # e.g. {"type": "ephemeral"} (Anthropic passthrough, G8)


@dataclass
class TextPart:
    text: str
    cache_control: CacheControl = None


@dataclass
class ImagePart:
    url: str | None = None
    b64: str | None = None
    mime: str = "image/png"
    detail: str | None = None


@dataclass
class ToolUsePart:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw_args: str | None = None  # original JSON string if provider gave one

    def __post_init__(self) -> None:
        # Providers/decoders can hand us a non-string id (OpenAI-compatible
        # gateways sometimes emit numeric ids). Wire dialects and adapters
        # must emit a string id, so coerce once at the IR boundary.
        if not isinstance(self.id, str):
            self.id = str(self.id)


@dataclass
class ToolResultPart:
    tool_use_id: str
    content: str
    is_error: bool = False
    cache_control: CacheControl = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_use_id, str):
            self.tool_use_id = str(self.tool_use_id)


@dataclass
class ThinkingPart:
    text: str
    signature: str | None = None


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
    name: str | None = None


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
    reasoning_effort: str | None = None  # "low" | "medium" | "high"
    thinking_budget: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def effective_reasoning_effort(self) -> str | None:
        """Return reasoning_effort, deriving it from thinking_budget if not set."""
        if self.reasoning_effort:
            return self.reasoning_effort
        if self.thinking_budget is not None:
            return thinking_budget_to_effort(self.thinking_budget)
        return None

    def effective_thinking_budget(self) -> int | None:
        """Return thinking_budget, deriving it from reasoning_effort if not set.

        Returns None when reasoning_effort is 'none' (thinking disabled) or
        when neither field is set, so adapters can distinguish 'no thinking'
        from 'use a default budget.'
        """
        if self.thinking_budget is not None:
            return self.thinking_budget
        if self.reasoning_effort:
            return effort_to_thinking_budget(self.reasoning_effort)
        return None


# Reasoning effort ↔ thinking budget mapping.
# Approximate token budgets per effort level. These are conservative defaults;
# providers that natively support effort levels (OpenAI) use the string directly,
# while providers that use token budgets (Anthropic) get the mapped value.
# "none" means disable thinking entirely (supported by GPT-5.x reasoning models).
_EFFORT_BUDGETS: dict[str, int | None] = {
    "none": None,
    "low": 1024,
    "medium": 8000,
    "high": 32000,
    "xhigh": 64000,
}


def effort_to_thinking_budget(effort: str) -> int | None:
    """Map a reasoning_effort level to a thinking token budget.

    Returns None for 'none' (thinking disabled) or unknown values; callers
    must check the return before setting a thinking config.
    """
    return _EFFORT_BUDGETS.get(effort, _EFFORT_BUDGETS["medium"])


def thinking_budget_to_effort(budget: int) -> str:
    """Map a thinking token budget to the nearest reasoning_effort level."""
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
    reasoning_estimated: bool = False
    # Anthropic cache bookkeeping (G8-adjacent accounting)
    cache_creation_tokens: int = 0


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


@dataclass
class Response:
    turn: AssistantTurn
    model: str = ""
    id: str = ""
