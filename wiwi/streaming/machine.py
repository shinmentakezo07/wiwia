"""The delta-stream state machine, made explicit and checkable.

START --StreamStart--> BODY --content / Open/Args/Close--> BODY
                       BODY --Finish--> FINISHED --StreamEnd--> DONE
   any --StreamError--> FAILED                            (terminal)

wiwi/streaming/deltas.py states this contract in prose: adapters guarantee it,
encoders rely on it, and encoders "never defend" against a violation. That is a
sound division of labour, but it left two gaps that this module closes without
changing either side's behaviour:

* Nothing could check the contract. A violation was invisible: an adapter that
  emitted a delta out of order produced a subtly malformed client stream and no
  log line said so. DeltaStreamState turns the prose into code, so the
  guarantee is testable for every adapter rather than trusted.
* Nothing could explain a stream. Debugging meant reading private attributes on
  three encoders (_open_block, _item_open, _tool_blocks, ...), each named
  differently for the same concept.

The machine is a pure observer: observe() returns a problem or None and never
raises, so wiring it into a hot path cannot break a response. Callers decide
what to do with a problem - log it, count it, or assert on it in a test.

Siblings and repeated usage are legal, per the contract: several tool indices may
be open at once and close in any order, and UsageFinal may arrive more than once
(encoders buffer last-write-wins).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from wiwi.streaming import deltas as dl

# The phases of a single response stream. BODY is the only phase that accepts
# content, tool lifecycle and usage deltas.
Phase = Literal["START", "BODY", "FINISHED", "DONE", "FAILED"]


@dataclass
class DeltaStreamState:
    """Tracks one stream's phase and reports the first contract violation.

    Usage::

        state = DeltaStreamState()
        for d in deltas:
            problem = state.observe(d)
            if problem is not None:
                ...  # log / count / raise in a test

    The state is deliberately inspectable (phase, open_tools, saw_usage),
    because "why did this body look wrong" is the question an agent asks while
    debugging a client report.
    """

    phase: Phase = "START"
    # Tool indices currently open. Several may be open at once (parallel
    # siblings) and they may close in ANY order - the contract only requires
    # that Args/Close name a call that is currently open.
    open_tools: set[int] = field(default_factory=set)
    saw_usage: bool = False
    # The first violation seen, kept so a test can assert after the fact
    # without threading a value through the caller's loop.
    problem: str | None = None

    @property
    def terminal(self) -> bool:
        """True once the stream can produce nothing further."""
        return self.phase in ("DONE", "FAILED")

    def observe(self, d: dl.IRStreamDelta) -> str | None:
        """Advance the machine by one delta; return a problem, or None.

        Never raises. A stream that has already reported a violation keeps its
        first problem - the useful question is always "what went wrong first".
        """
        if self.problem is not None:
            return None
        problem = self._advance(d)
        if problem is not None:
            self.problem = problem
        return problem

    def _advance(self, d: dl.IRStreamDelta) -> str | None:
        # StreamError is the one delta legal from ANY phase, including after a
        # Finish: it is the abnormal terminal and replaces everything after the
        # last emitted delta.
        if isinstance(d, dl.StreamError):
            self.phase = "FAILED"
            self.open_tools.clear()
            return None
        if self.terminal:
            return (f"{type(d).__name__} after the stream already ended "
                    f"(phase={self.phase})")
        if isinstance(d, dl.StreamStart):
            if self.phase == "BODY":
                return "StreamStart repeated (the contract allows exactly one)"
            # Not START and not BODY: the stream already finished. Report the
            # phase rather than claiming a duplicate, so the message names the
            # real mistake.
            if self.phase != "START":
                return f"StreamStart after the stream ended (phase={self.phase})"
            self.phase = "BODY"
            return None
        if self.phase == "START":
            return f"{type(d).__name__} before StreamStart"
        if isinstance(d, dl.ToolCallOpen):
            if d.index in self.open_tools:
                return f"ToolCallOpen re-opened tool index {d.index}"
            self.open_tools.add(d.index)
            return None
        if isinstance(d, dl.ToolCallArgsDelta):
            if d.index not in self.open_tools:
                return f"ToolCallArgsDelta for closed tool index {d.index}"
            return None
        if isinstance(d, dl.ToolCallClose):
            if d.index not in self.open_tools:
                return f"ToolCallClose for closed tool index {d.index}"
            self.open_tools.discard(d.index)
            return None
        if isinstance(d, dl.UsageFinal):
            # May repeat, and may precede the last content delta: the contract
            # says so explicitly, and every encoder buffers last-write-wins.
            self.saw_usage = True
            return None
        if isinstance(d, dl.Finish):
            if self.phase != "BODY":
                return f"Finish in phase {self.phase}"
            self.phase = "FINISHED"
            # A Finish with calls still open is legal: adapters finish the turn
            # and the encoder closes the group.
            return None
        if isinstance(d, dl.StreamEnd):
            if self.phase != "FINISHED":
                return f"StreamEnd without Finish (phase={self.phase})"
            self.phase = "DONE"
            return None
        # Content and any other body delta. Legal in BODY, and also after a
        # Finish - UsageFinal "may precede the last content delta", so a late
        # content delta must not be reported as a violation.
        return None

    def explain(self) -> str:
        """A one-line summary, for a log line or a test failure message."""
        if self.problem is not None:
            return f"INVALID phase={self.phase} problem={self.problem}"
        tools = f" open_tools={sorted(self.open_tools)}" if self.open_tools else ""
        return f"{self.phase} usage={self.saw_usage}{tools}"


def validate_stream(deltas: list[dl.IRStreamDelta]) -> str | None:
    """Check a completed delta list; return the first violation or None.

    The test-facing entry point: an adapter can be driven with a recorded
    upstream response and asserted to produce a legal stream.
    """
    state = DeltaStreamState()
    for d in deltas:
        state.observe(d)
    if state.problem is not None:
        return state.problem
    # DONE (Finish then StreamEnd) and FAILED (StreamError) are both complete.
    # FAILED is the abnormal terminal and legitimately has no StreamEnd, so it
    # must not be reported as a truncation.
    if not state.terminal:
        return f"stream ended in phase {state.phase} (no StreamEnd)"
    return None

def explain_encoder(open_item: str | None, open_tools: Iterable[int],
                    deferred_items: int, deferred_chars: int,
                    tool_calls: int, usage: bool) -> str:
    """One-line encoder state, shared by every stream encoder.

    The encoders hold the same conceptual state - which block or item is open,
    which tool calls are live, how much prose is deferred waiting for them -
    under three different private vocabularies (_open_block/_tool_blocks,
    _item_open/_tools, _open_calls/_deferred). Reading that state meant knowing
    all three, and tests coupled to the private names broke on any rename. This
    gives every encoder one stable summary that logs and tests can rely on.
    """
    parts = [f"open={open_item or 'none'}", f"tool_calls={tool_calls}"]
    open_list = sorted(open_tools)
    if open_list:
        parts.append(f"open_tools={open_list}")
    if deferred_items:
        parts.append(f"deferred={deferred_items}item/{deferred_chars}ch")
    parts.append(f"usage={'buffered' if usage else 'none'}")
    return " ".join(parts)
