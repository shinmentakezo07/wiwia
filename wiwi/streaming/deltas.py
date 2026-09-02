"""IRStreamDelta taxonomy — the contract for the streaming pump (docs/CORE.md §7.1).

Ordering contract (adapters guarantee, encoders rely on):
  exactly one StreamStart first;
  ToolCallOpen -> ToolCallArgsDelta* -> ToolCallClose strictly nested per index;
  UsageFinal exactly once, after the last content delta;
  then Finish;
  then exactly one of StreamEnd | StreamError.
StreamError may terminate at ANY point, replacing everything after the last
emitted delta — it is the abnormal-path terminal and needs no Finish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wiwi.ir.types import StopReason


@dataclass(frozen=True)
class StreamStart:
    model: str
    group: str = ""


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    text: str
    signature: str | None = None


@dataclass(frozen=True)
class ToolCallOpen:
    index: int
    id: str
    name: str


@dataclass(frozen=True)
class ToolCallArgsDelta:
    index: int
    args_fragment: str


@dataclass(frozen=True)
class ToolCallClose:
    index: int


@dataclass(frozen=True)
class UsageFinal:
    prompt: int = 0
    cached: int = 0
    reasoning: int = 0
    output: int = 0
    cache_creation: int = 0
    estimated: bool = False
    cost: float = 0.0


@dataclass(frozen=True)
class Finish:
    stop_reason: StopReason = "stop"
    stop_sequence: str | None = None  # matched stop sequence (Anthropic surfaces it)


@dataclass(frozen=True)
class StreamEnd:
    pass


@dataclass(frozen=True)
class StreamError:
    message: str
    kind: Literal["timeout", "connection", "status", "cancelled", "unknown"] = "unknown"
    status: int | None = None


IRStreamDelta = (
    StreamStart | TextDelta | ThinkingDelta | ToolCallOpen | ToolCallArgsDelta
    | ToolCallClose | UsageFinal | Finish | StreamEnd | StreamError
)
