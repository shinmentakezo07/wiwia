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


@dataclass
class ToolResultPart:
    tool_use_id: str
    content: str
    is_error: bool = False
    cache_control: CacheControl = None


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
    strict: bool | None = None  # G3: OpenAI structured-output strictness


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
    reasoning_effort: str | None = None  # "low" | "medium" | "high"
    thinking_budget: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Request:
    model: str
    messages: list[Message]
    tools: list[Tool] = field(default_factory=list)
    tool_choice: ToolChoice | None = None
    gen_params: GenParams = field(default_factory=GenParams)
    stream: bool = False
    stream_options_include_usage: bool = True  # G4
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
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] | None = None  # provider-native response for passthrough extras


@dataclass
class Response:
    turn: AssistantTurn
    model: str = ""
    id: str = ""
