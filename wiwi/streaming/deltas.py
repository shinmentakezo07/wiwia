"""IRStreamDelta taxonomy — the contract for the streaming pump (docs/CORE.md §7.1).

Ordering contract (adapters guarantee, encoders rely on):
  exactly one StreamStart first;
  ToolCallOpen -> ToolCallArgsDelta* -> ToolCallClose, nested per index;
  a ServerToolResultDelta may follow the Close of its own server-tool index;
  UsageFinal at least once, after content;
  then Finish;
  then exactly one of StreamEnd | StreamError.
StreamError may terminate at ANY point, replacing everything after the last
emitted delta — it is the abnormal-path terminal and needs no Finish.

Two places where the real streams are looser than the shape above, both
absorbed downstream so the client still sees a well-formed stream:

* Parallel tool calls are SIBLINGS, not strictly nested. Adapters may hold
  several indices open at once and close them in a batch (OpenAI closes every
  open index at ``finish_reason``; Gemini emits Open/Args/Close per
  ``functionCall`` part). Encoders must not assume one index at a time.
* UsageFinal may arrive MORE THAN ONCE and before the last content delta:
  OpenAI, NIM and Gemini all attach usage to intermediate chunks. Every encoder
  buffers it last-write-wins and emits once from its terminal frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from wiwi.ir.types import StopReason, ToolUseBlockType


@dataclass(frozen=True)
class StreamStart:
    model: str
    group: str = ""
    # Anthropic reports prompt/cache usage in ``message_start``, before any
    # content, and Claude Code reads it there to drive its context meter and
    # auto-compact decision. The IR had no carrier for it, so the encoder had
    # to emit zeros and the client's running total stayed at 0 for the whole
    # session (AUDIT #156). Providers that only report usage at the end leave
    # these at 0 and the encoder falls back to the trailing UsageFinal.
    prompt: int = 0
    cached: int = 0
    cache_creation: int = 0


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    text: str
    signature: str | None = None
    # Anthropic can emit a ``redacted_thinking`` block — an opaque encrypted
    # blob replayed verbatim on the next turn. ``block_type`` marks the delta
    # as such and ``data`` carries the blob, so the streaming encoder can
    # re-emit a redacted_thinking block instead of dropping it (AUDIT #103).
    block_type: str | None = None
    data: str | None = None
    # OpenAI Responses ``reasoning.encrypted_content``: the opaque blob the
    # Responses API hands back and demands back on the next turn. Codex
    # streams its turns, so the streaming path needs the same carrier the IR
    # has for the non-streaming one (AUDIT #355).
    encrypted_content: str | None = None


@dataclass(frozen=True)
class ToolCallOpen:
    index: int
    id: str
    name: str
    # Canonical builtin name (ir/builtin_tools.py) when this call invokes a
    # provider-hosted tool (e.g. Anthropic server_tool_use for web_search).
    # Encoders use it to suppress (A1) or re-render as a hosted item.
    builtin: str | None = None
    # Original wire block type (``tool_use`` | ``server_tool_use`` |
    # ``mcp_tool_use``). Replay must re-emit the same spelling or the paired
    # result block is unpaired and the API rejects the history.
    block_type: ToolUseBlockType = "tool_use"


@dataclass(frozen=True)
class ToolCallArgsDelta:
    index: int
    args_fragment: str


@dataclass(frozen=True)
class ToolCallClose:
    index: int


@dataclass(frozen=True)
class ServerToolResultDelta:
    """A provider-executed tool's RESULT block, carried verbatim.

    Anthropic streams a server tool's result as its own content block
    (``web_search_tool_result``, ``tool_search_tool_result``, ``mcp_tool_result``)
    immediately after the call block. The IR had no delta for it, so the block
    was dropped on decode and the client never saw the search results or the
    discovered-tool references it carries.

    Modelled as a whole block rather than Open/Args/Close because it is
    delivered whole upstream and has no partial-JSON lifecycle to preserve.
    ``index`` is the block index so an encoder that emits it can keep the
    client's numbering; encoders for dialects with no equivalent block (Chat,
    Responses, Gemini) ignore it.
    """

    index: int
    block: dict[str, Any]
    builtin: str | None = None


@dataclass(frozen=True)
class LogprobsDelta:
    """Per-token log probabilities for the content just emitted.

    The streaming half of the AUDIT #326 carrier. The sync path holds
    logprobs on ``AssistantTurn.logprobs``; the streaming path needs its own
    delta because a token's logprobs arrive with that token, not at the end
    of the turn.

    ``logprobs`` is the provider's own ``choices[0].logprobs`` dict
    (``{"content": [{"token", "logprob", "bytes", "top_logprobs"}], ...}``) —
    the OpenAI wire shape, carried verbatim for the same reason the IR field
    is: a second dialect would have to translate it before a caller could
    read it, so there is nothing to gain by modelling it twice.
    """

    logprobs: dict[str, Any] | None = None


@dataclass(frozen=True)
class UsageFinal:
    prompt: int = 0
    cached: int = 0
    reasoning: int = 0
    output: int = 0
    cache_creation: int = 0
    estimated: bool = False
    cost: float = 0.0
    # Streaming half of ``ir.Usage.cached_in_prompt``: True when ``prompt``
    # already contains ``cached``. ``None`` means this adapter did not record
    # the convention, and pricing falls back to the deployment's wire shape.
    # Without it, the streaming path — which is how Claude Code, Codex and the
    # playground are all served — could not express the fact the sync path now
    # carries, so the fix would have been inert for every real client.
    cached_in_prompt: bool | None = None


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
    # The provider's OWN error type when it named one (Anthropic's
    # ``overloaded_error``, ``rate_limit_error``, …). A mid-stream error frame
    # carries no HTTP status, so ``kind``/``status`` alone cannot recover the
    # classification the client retries on; this preserves it verbatim.
    etype: str | None = None


def stream_error_type(d: StreamError) -> str:
    """wiwi's classification of a mid-stream failure, for any dialect to render.

    A mid-stream ``error`` frame carries no HTTP status, so the classification
    a client retries on has to be recovered from the delta itself: the vendor's
    own type when the upstream named one (``etype``), else the status the
    gateway observed, else the failure kind. The vocabulary is the one
    ``providers/base.error_from_provider_status`` puts on ``WiwiError.etype``,
    so a streamed failure and a non-streamed one are classified the same way.

    This lives beside the delta it interprets because every dialect encoder
    needs it and each spells the result differently: the OpenAI surfaces pass
    it straight into ``error.type``/``error.code`` — exactly what their sync
    ``error_body`` does with the same etype — while the Anthropic encoder maps
    ``timeout`` onto its own ``timeout_error``. Before this helper existed the
    two OpenAI-shaped codecs hardcoded ``api_error`` for every mid-stream
    failure, so a 429/529/timeout reached an OpenAI client unclassified while
    the identical failure reached Claude Code correctly classified, depending
    only on which surface the caller used (AUDIT #478).
    """
    if d.etype:
        return d.etype
    if d.status == 529:
        return "overloaded_error"
    if d.status == 429:
        return "rate_limit_error"
    if d.status == 504 or d.kind == "timeout":
        return "timeout"
    if d.status in (408, 500, 502, 503):
        return "api_connection_error"
    return "api_error"


IRStreamDelta = (
    StreamStart | TextDelta | ThinkingDelta | ToolCallOpen | ToolCallArgsDelta
    | ToolCallClose | ServerToolResultDelta | LogprobsDelta | UsageFinal
    | Finish | StreamEnd | StreamError
)
