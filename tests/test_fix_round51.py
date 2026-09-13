"""Round-51 regression tests — the NIM synthesize branch emits an unusable Open.

One defect, found by grepping for the *shape* of a bug fixed in two sibling
adapters minutes earlier (AUDIT #135, second defect) rather than by running
anything — the same branch turned out to exist in a third adapter.

- #143 ``NimAdapter``'s args-before-id synthesize branch emitted
  ``ToolCallOpen(index=idx, id="", name="")`` — a literal empty name — while
  storing the real name fragment in ``self._tool_names[idx]`` one line above.
  ``OpenAIAdapter`` and ``OpenRouterAdapter`` carried the identical bug and
  were fixed in round 49; NIM is the third site, missed because that entry
  scoped itself to "both adapters".

  Measured, one frame (name + args, no id) through both adapters:

      NimAdapter     -> [('', '')]
      OpenAIAdapter  -> [('', 'get_weather')]

  ``AnthropicStreamEncoder`` renders the Open as a ``tool_use`` block, and a
  block with ``name: ""`` cannot be dispatched by any client — while the
  upstream still bills for the call.
"""

from __future__ import annotations

import orjson

from wiwi.providers.nim_adapter import NimAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter


def _chunk(**delta) -> str:
    return orjson.dumps({"choices": [{"index": 0, "delta": delta}]}).decode()


def _opens(adapter) -> list[tuple[str, str]]:
    """(id, name) for every ToolCallOpen the adapter emits."""
    out = adapter.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "function": {"name": "get_weather",
                                  "arguments": '{"city":"Paris"}'}}]))
    return [(d.id, d.name) for d in out
            if type(d).__name__ == "ToolCallOpen"]


def test_nim_synthesized_open_carries_the_tool_name():
    """The synthesized Open must carry the name it was given.

    Pre-fix it emitted a literal ``name=""`` while the real fragment sat in
    ``_tool_names[idx]``, so the client got an undispatchable tool block.
    """
    ad = NimAdapter()
    ad.reset()
    assert _opens(ad) == [("", "get_weather")], (
        "the synthesized Open must carry the tool name, not an empty string"
    )


def test_nim_synthesized_open_matches_openai_adapter():
    """Control-by-parity: the same frame must produce the same Open in both.

    The three adapters share this branch by design, so a divergence here is a
    regression in whichever side moved — this is the assertion that would have
    caught the original bug without knowing which adapter was wrong.
    """
    nim, oai = NimAdapter(), OpenAIAdapter()
    nim.reset()
    oai.reset()
    assert _opens(nim) == _opens(oai), (
        "NIM and OpenAI disagree on the synthesized Open for one frame"
    )


def test_nim_nameless_synthesized_open_stays_empty():
    """Control: a chunk with args but genuinely no name must still open.

    ``name=""`` is correct when the upstream never sent a fragment — the
    contract requires the Open regardless, and the point of the fix is to stop
    discarding a name that *was* sent, not to invent one.
    """
    ad = NimAdapter()
    ad.reset()
    out = ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "function": {"arguments": '{"city":"Paris"}'}}]))
    opens = [d for d in out if type(d).__name__ == "ToolCallOpen"]
    assert [(d.id, d.name) for d in opens] == [("", "")], (
        "a nameless chunk must still synthesize an Open, with an empty name"
    )
    assert any(type(d).__name__ == "ToolCallArgsDelta" for d in out), (
        "the args delta must still follow the synthesized Open"
    )
