"""StreamTape: bounded ring-buffer of emitted deltas for mid-stream failover
and client-side SSE resumption.

Two roles:
1. **Mid-stream failover**: on upstream death after content has flowed, the tape
   holds the text deltas already emitted so a retry can prepend them as an
   assistant-prefix continuation request (Anthropic capture-and-resume pattern).
2. **Last-Event-ID replay**: when a client reconnects with ``Last-Event-ID``,
   the tape replays missed deltas.

The tape is bounded by max_bytes (default 256 KiB). When full, oldest deltas
are evicted — this means only the most recent ~256 KiB of streamed content is
available for resume/replay, which is sufficient for continuation requests.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from wiwi.streaming import deltas as dl

# Sentinel for deltas that don't carry client-visible content (control only).
_SKIP_TAPE_TYPES = (dl.StreamStart, dl.StreamEnd, dl.StreamError)


@dataclass
class TapeEntry:
    """One delta + the monotonic event id assigned at emission time."""
    seq: int
    delta: dl.IRStreamDelta


class StreamTape:
    """Bounded ring-buffer of emitted deltas.

    Stores content-bearing deltas (TextDelta, ThinkingDelta, ToolCallOpen,
    ToolCallArgsDelta, ToolCallClose, UsageFinal, Finish) with monotonic
    sequence numbers. Control deltas (StreamStart/End/Error) are not stored.
    """

    def __init__(self, max_bytes: int = 256 * 1024) -> None:
        self._entries: deque[TapeEntry] = deque()
        self._seq = 0
        self._bytes = 0
        self._max_bytes = max_bytes

    @property
    def seq(self) -> int:
        """Next sequence number to assign."""
        return self._seq

    @property
    def bytes(self) -> int:
        return self._bytes

    def append(self, delta: dl.IRStreamDelta) -> int:
        """Record *delta* and return its assigned sequence number."""
        if isinstance(delta, _SKIP_TAPE_TYPES):
            return self._seq
        self._seq += 1
        entry = TapeEntry(self._seq, delta)
        self._entries.append(entry)
        self._bytes += _delta_size(delta)
        self._evict()
        return self._seq

    def replay(self, last_seq: int = 0) -> list[dl.IRStreamDelta]:
        """Return all deltas with seq > *last_seq* in order."""
        return [e.delta for e in self._entries if e.seq > last_seq]

    def replay_text(self) -> str:
        """Concatenate all TextDelta text from the tape (for continuation)."""
        return "".join(
            e.delta.text for e in self._entries if isinstance(e.delta, dl.TextDelta)
        )

    def replay_thinking(self) -> str:
        """Concatenate all ThinkingDelta text from the tape (for continuation)."""
        return "".join(
            e.delta.text for e in self._entries
            if isinstance(e.delta, dl.ThinkingDelta) and e.delta.text
        )

    def replay_tool_calls(self) -> list:
        """Reconstruct partial ToolUseParts from taped tool-call deltas.

        Folds ToolCallOpen/ArgsDelta/Close into ToolUsePart objects with
        accumulated (and auto-repaired) args, preserving order.  Used by
        build_continuation_messages so a resumed stream does not re-emit
        tool calls the client already received.
        """
        import json

        from wiwi.ir import types as ir
        from wiwi.streaming.partial_json import _repair_truncated_json
        out: list[ir.ToolUsePart] = []
        open_calls: dict[int, ir.ToolUsePart] = {}
        arg_bufs: dict[int, str] = {}
        for e in self._entries:
            d = e.delta
            if isinstance(d, dl.ToolCallOpen):
                open_calls[d.index] = ir.ToolUsePart(id=d.id, name=d.name, args={})
                arg_bufs[d.index] = ""
            elif isinstance(d, dl.ToolCallArgsDelta):
                if d.index in arg_bufs:
                    arg_bufs[d.index] += d.args_fragment
            elif isinstance(d, dl.ToolCallClose):
                tc = open_calls.pop(d.index, None)
                raw = arg_bufs.pop(d.index, "")
                if tc is not None:
                    if raw:
                        try:
                            tc.args = json.loads(_repair_truncated_json(raw))
                        except (json.JSONDecodeError, ValueError):
                            tc.raw_args = raw
                    else:
                        tc.raw_args = raw
                    out.append(tc)
        # Flush any still-open tool calls (stream died mid-tool-call).
        for idx in sorted(open_calls):
            tc = open_calls[idx]
            raw = arg_bufs.get(idx, "")
            if raw:
                try:
                    tc.args = json.loads(_repair_truncated_json(raw))
                except (json.JSONDecodeError, ValueError):
                    tc.raw_args = raw
            else:
                tc.raw_args = raw
            out.append(tc)
        return out

    def _evict(self) -> None:
        while self._bytes > self._max_bytes and self._entries:
            evicted = self._entries.popleft()
            self._bytes -= _delta_size(evicted.delta)

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0


def _delta_size(delta: dl.IRStreamDelta) -> int:
    """Approximate byte size of a delta for tape accounting."""
    if isinstance(delta, dl.TextDelta):
        return len(delta.text)
    if isinstance(delta, dl.ThinkingDelta):
        return len(delta.text) + len(delta.signature or "")
    if isinstance(delta, dl.ToolCallOpen):
        return len(delta.id) + len(delta.name) + 8
    if isinstance(delta, dl.ToolCallArgsDelta):
        return len(delta.args_fragment)
    if isinstance(delta, dl.ToolCallClose):
        return 4
    if isinstance(delta, dl.UsageFinal):
        return 32
    if isinstance(delta, dl.Finish):
        return 8
    return 0




def build_continuation_messages(
    tape: StreamTape,
    original_messages: list,
) -> list:
    """Build messages for a continuation request after mid-stream failure.

    Appends the partial assistant response (text, thinking, and any partial
    tool calls) as an assistant message, so the upstream continues from
    where the previous attempt left off.  Without the partial tool calls,
    the model would re-emit them on resume, duplicating tool calls the
    client already received.

    *original_messages* is a list of ``ir.Message``; the returned list is a
    new list with the assistant continuation appended.
    """
    from wiwi.ir import types as ir

    text = tape.replay_text()
    thinking = tape.replay_thinking()
    # Reconstruct partial tool calls from the tape deltas.
    tool_calls = tape.replay_tool_calls()
    msgs = list(original_messages)
    parts: list[ir.Part] = []
    if thinking:
        parts.append(ir.ThinkingPart(thinking))
    if text:
        parts.append(ir.TextPart(text))
    parts.extend(tool_calls)
    if parts:
        msgs.append(ir.Message(role="assistant", parts=parts))
        # Add a minimal user message asking the model to continue.
        msgs.append(ir.Message(role="user", parts=[ir.TextPart("Continue from where you left off.")]))
    return msgs
