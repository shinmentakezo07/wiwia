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

    def head_evicted(self, last_seq: int) -> bool:
        """True when eviction removed entries the continuation needs.

        AUDIT #68: a continuation request replays ``last_seq``-plus-survivors
        as the assistant prefix. If entries between ``last_seq`` and the
        first survivor were evicted, the prefix is silently partial (e.g. a
        tool call whose Open was evicted while its Args/Close survive — the
        model would re-invoke a tool the client already saw). Only provable
        when the first surviving seq is not exactly ``last_seq + 1`` AND
        the tape still has a head below it; an empty replay is vacuously
        fine (the client saw nothing it needs continued).
        """
        if not self._entries:
            return False
        first = self._entries[0].seq
        # Contiguous when replay(last_seq) starts exactly at the successor.
        return first > last_seq + 1

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

    def replay_thinking_parts(self) -> list:
        """Reconstruct structured ThinkingParts from taped ThinkingDeltas.

        Continuation requests replay the partial assistant turn upstream, so
        the thinking blocks must survive with the fidelity the wire carried
        (AUDIT #118): consecutive thinking deltas are folded into one block
        whose ``signature`` is the last signature seen in the run (Anthropic
        sends ``signature_delta`` at block end), and ``redacted_thinking``
        deltas re-emerge as their own opaque block. A bare-text part drops
        both, and the resumed request is rejected upstream — a signed
        thinking block without its signature 400s, and a redacted block is
        mandatory before tool use on some turns.
        """
        from wiwi.ir import types as ir

        parts: list[ir.ThinkingPart] = []
        text_bits: list[str] = []
        signature: str | None = None

        def _flush() -> None:
            nonlocal text_bits, signature
            joined = "".join(text_bits)
            if joined:
                parts.append(ir.ThinkingPart(joined, signature=signature))
            text_bits = []
            signature = None

        for e in self._entries:
            d = e.delta
            if not isinstance(d, dl.ThinkingDelta):
                # A content delta ends the thinking run: the next thinking
                # delta opens a NEW block whose signature must not be glued
                # onto the previous run's text.
                _flush()
                continue
            if d.block_type == "redacted_thinking":
                _flush()
                parts.append(ir.ThinkingPart("", block_type="redacted_thinking",
                                             data=d.data))
                continue
            if d.text:
                text_bits.append(d.text)
            if d.signature:
                signature = d.signature
        _flush()
        return parts

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
        # List buffers joined once per close (AUDIT #104: repeated string
        # concatenation is O(n^2) on long argument streams).
        arg_bufs: dict[int, list[str]] = {}
        for e in self._entries:
            d = e.delta
            if isinstance(d, dl.ToolCallOpen):
                open_calls[d.index] = ir.ToolUsePart(id=d.id, name=d.name, args={})
                arg_bufs[d.index] = []
            elif isinstance(d, dl.ToolCallArgsDelta):
                if d.index in arg_bufs:
                    arg_bufs[d.index].append(d.args_fragment)
            elif isinstance(d, dl.ToolCallClose):
                tc = open_calls.pop(d.index, None)
                raw = "".join(arg_bufs.pop(d.index, []))
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
            raw = "".join(arg_bufs.get(idx, []))
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
        return len(delta.text) + len(delta.signature or "") + len(delta.data or "")
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
    thinking_parts = tape.replay_thinking_parts()
    # Reconstruct partial tool calls from the tape deltas.
    tool_calls = tape.replay_tool_calls()
    msgs = list(original_messages)
    parts: list[ir.Part] = []
    parts.extend(thinking_parts)
    if text:
        parts.append(ir.TextPart(text))
    parts.extend(tool_calls)
    if parts:
        msgs.append(ir.Message(role="assistant", parts=parts))
        # A continuation user turn must answer any tool_use in the partial
        # assistant message: Anthropic rejects an assistant turn whose tool_use
        # blocks have no matching tool_result in the following user turn
        # (AUDIT #107). Synthesize a placeholder result — the real execution
        # happens client-side, but the resume only needs a well-formed history
        # so the model can continue.
        follow_up: list[ir.Part] = []
        for tc in tool_calls:
            follow_up.append(ir.ToolResultPart(
                tool_use_id=tc.id, content="(continuing)",
                block_type="tool_result"))
        follow_up.append(ir.TextPart("Continue from where you left off."))
        msgs.append(ir.Message(role="user", parts=follow_up))
    return msgs
