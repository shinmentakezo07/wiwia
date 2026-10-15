"""Round 154: five defects in `wiwi/wire/`, found by a source review of the
wire codecs and reproduced against the real encoders before fixing.

Three live in the Responses stream encoder, which buffers content that arrives
while a tool item is open (the #367 policy):

* **#374** the flush only ran on the *function-call* return path; the hosted
  (builtin) branch of `_close_tool` returned early, so narrative text written
  around a `web_search_call` was buffered and never emitted.
* **#375** `_flush_deferred` emitted one output item per buffered entry despite
  a docstring promising adjacent same-kind entries coalesce — and every
  message item carried the same `msg_<req>` id, so a coalesced or interleaved
  flush produced duplicate item ids that a client accumulating by id cannot
  tell apart.
* **#376** a single delta larger than `MAX_DEFERRED_CHARS` was popped whole by
  the eviction loop, and worse, the loop kept popping past the cap so the
  buffer was emptied entirely — discarding prior in-flight content that was
  nowhere near the cap. The Anthropic encoder already had the overshoot
  handling this one lacked; the fix ports that shape.

Two are in the Anthropic codec:

* **#398** the streaming `message_delta` usage omitted
  `output_tokens_details.thinking_tokens` while the sync body emitted it, so a
  client that meters thinking tokens read zero on streamed turns and the real
  count on non-streamed ones, for the same request.
* **new** the `document` decode arm passed a block's `title`/`context` into the
  IR with no type check, unlike the `filename` guard `openai_responses.py:83`
  already carried for the same class of bug (AUDIT #184). A client sending
  `"title": 5` had the junk stored and re-sent on every later turn.

Every test below failed before the fix.
"""

from __future__ import annotations

import json

from wiwi.ir import types as ir
from wiwi.providers.registry import fresh_adapter
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_responses as orsp

# --- helpers ---------------------------------------------------------------

def _events(raw: bytes) -> list[dict]:
    return [json.loads(line[6:]) for line in raw.decode().splitlines()
            if line.startswith("data: ")]


def _run_responses(deltas: list[dl.IRStreamDelta]) -> bytes:
    """Drive the encoder the way `server/app.py:2792` does: feed, then the
    terminal event."""
    enc = orsp.ResponsesStreamEncoder("m", "R")
    out = b"".join(enc.feed(d) or b"" for d in deltas)
    return out + enc._completed()


def _done_items(raw: bytes, item_type: str) -> list[dict]:
    return [e["item"] for e in _events(raw)
            if e.get("type") == "response.output_item.done"
            and e["item"]["type"] == item_type]


def _anthropic_stream_usage(deltas: list[dl.IRStreamDelta]) -> dict:
    enc = am.AnthropicStreamEncoder("claude", "R")
    out = b"".join(enc.feed(d) or b"" for d in deltas)
    out += enc.final_frame()
    return [e for e in _events(out)
            if e.get("type") == "message_delta"][-1]["usage"]


def _document_request(title: object, context: object) -> dict:
    """A Messages request with a base64 document carrying the given fields."""
    block: dict = {"type": "document",
                   "source": {"type": "base64",
                              "media_type": "application/pdf", "data": "QQ=="}}
    if title is not None:
        block["title"] = title
    if context is not None:
        block["context"] = context
    return {"model": "m", "max_tokens": 10,
            "messages": [{"role": "user", "content": [block]}]}


def _outbound_document(req: ir.Request) -> dict:
    body = fresh_adapter("anthropic").encode_request(req, "claude-x", {})
    return next(b for b in body["messages"][0]["content"]
                if isinstance(b, dict) and b.get("type") == "document")


# --- the Anthropic document arm's unguarded title/context -------------------

def test_anthropic_document_non_str_title_and_context_are_dropped():
    """`name`/`context` are typed `str | None`; junk must become absent, not be
    forwarded (the #184 shape, missed on this arm)."""
    req = am.decode_request(_document_request(5, ["a", "b"]))
    part = req.messages[0].parts[0]
    assert part.name is None, f"non-str title stored: {part.name!r}"
    assert part.context is None, f"non-str context stored: {part.context!r}"
    doc = _outbound_document(req)
    assert "title" not in doc, f"junk title forwarded: {doc.get('title')!r}"
    assert "context" not in doc, f"junk context forwarded: {doc.get('context')!r}"


def test_anthropic_document_real_title_and_context_survive():
    """The guard must not cost a legitimate document its metadata."""
    req = am.decode_request(_document_request("Q3 report", "internal only"))
    part = req.messages[0].parts[0]
    assert part.name == "Q3 report"
    assert part.context == "internal only"
    doc = _outbound_document(req)
    assert doc["title"] == "Q3 report"
    assert doc["context"] == "internal only"


def test_anthropic_document_url_arm_is_guarded_too():
    """Both DocumentPart arms carried the same unguarded reads."""
    req = am.decode_request({
        "model": "m", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "document",
             "source": {"type": "url", "url": "https://example.com/a.pdf"},
             "title": 7, "context": 9}]}]})
    part = req.messages[0].parts[0]
    assert part.url == "https://example.com/a.pdf"
    assert part.name is None
    assert part.context is None


# --- #374: deferred text during a hosted (builtin) tool call ----------------

def test_responses_builtin_tool_close_flushes_deferred_text():
    """Text written while a hosted tool call is open must reach the client."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="w1", name="web_search",
                        builtin="web_search"),
        dl.TextDelta(text="SEARCHING"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"query": "x"}'),
        dl.ToolCallClose(index=0),
    ])
    assert b"SEARCHING" in raw, "deferred text dropped on the builtin close path"
    items = _done_items(raw, "message")
    assert any("SEARCHING" in i["content"][0]["text"] for i in items)


def test_responses_function_tool_close_still_flushes():
    """Guard against regressing the path that already worked (AUDIT #367)."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c1", name="get_weather"),
        dl.TextDelta(text="SEARCHING"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"city": "x"}'),
        dl.ToolCallClose(index=0),
    ])
    assert b"SEARCHING" in raw


def test_responses_builtin_flush_waits_for_a_parallel_sibling():
    """With a second tool still open the flush must wait, or a message item
    lands between two tools' done events."""
    enc = orsp.ResponsesStreamEncoder("m", "R")
    out = b""
    for d in [dl.StreamStart(model="m"),
              dl.ToolCallOpen(index=0, id="w1", name="web_search",
                              builtin="web_search"),
              dl.ToolCallOpen(index=1, id="c1", name="fn"),
              dl.TextDelta(text="NARRATIVE")]:
        out += enc.feed(d) or b""
    out += enc.feed(dl.ToolCallClose(index=0)) or b""
    assert b"NARRATIVE" not in out, "flushed while a sibling tool was still open"
    out += enc.feed(dl.ToolCallClose(index=1)) or b""
    assert b"NARRATIVE" in out, "never flushed after the last tool closed"


# --- #375: coalescing adjacent deferred chunks ------------------------------

def test_responses_deferred_text_coalesces_adjacent_chunks():
    """Two adjacent text chunks must yield ONE output item."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c1", name="fn"),
        dl.TextDelta(text="AAA"),
        dl.TextDelta(text="BBB"),
        dl.ToolCallClose(index=0),
    ])
    items = _done_items(raw, "message")
    assert len(items) == 1, f"expected 1 coalesced item, got {len(items)}"
    assert items[0]["content"][0]["text"] == "AAABBB"


def test_responses_deferred_all_text_run_collapses():
    """A long run of text deltas is one item, not one per delta."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c1", name="fn"),
        *[dl.TextDelta(text=c) for c in "hello world"],
        dl.ToolCallClose(index=0),
    ])
    items = _done_items(raw, "message")
    assert len(items) == 1
    assert items[0]["content"][0]["text"] == "hello world"


def test_responses_kind_change_still_splits_items():
    """text→thinking is two items; only same-kind runs coalesce."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c1", name="fn"),
        dl.TextDelta(text="AAA"),
        dl.ThinkingDelta(text="TTT"),
        dl.TextDelta(text="BBB"),
        dl.ToolCallClose(index=0),
    ])
    msgs = _done_items(raw, "message")
    thinks = _done_items(raw, "reasoning")
    assert len(msgs) == 2, f"text runs should split around thinking, got {len(msgs)}"
    assert len(thinks) == 1
    assert [m["content"][0]["text"] for m in msgs] == ["AAA", "BBB"]


def test_responses_unique_item_ids_across_coalesced_runs():
    """Each emitted item needs its own id — duplicates break a client that
    accumulates output items by id."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c1", name="fn"),
        dl.TextDelta(text="AAA"),
        dl.ThinkingDelta(text="TTT"),
        dl.TextDelta(text="BBB"),
        dl.ToolCallClose(index=0),
    ])
    ids = [e["item"]["id"] for e in _events(raw)
           if e.get("type") == "response.output_item.done"]
    assert len(ids) == len(set(ids)), f"duplicate output item ids: {ids}"


def test_responses_unique_item_ids_across_a_tool_boundary():
    """The pre-existing twin of the above: the message item before a tool and
    the deferred one flushed after it must not share an id either."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.TextDelta(text="BEFORE"),
        dl.ToolCallOpen(index=0, id="c1", name="fn"),
        dl.TextDelta(text="DURING"),
        dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
        dl.ToolCallClose(index=0),
    ])
    msgs = _done_items(raw, "message")
    ids = [m["id"] for m in msgs]
    assert len(msgs) == 2, f"expected before/after items, got {len(msgs)}"
    assert len(ids) == len(set(ids)), f"duplicate message item ids: {ids}"


def test_responses_added_and_done_ids_agree():
    """An item's `response.output_item.added` id must equal its done id, or the
    client is left with an item that never closes."""
    raw = _run_responses([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c1", name="fn"),
        dl.TextDelta(text="DURING"),
        dl.ToolCallClose(index=0),
    ])
    added = [e["item"]["id"] for e in _events(raw)
             if e.get("type") == "response.output_item.added"
             and e["item"]["type"] == "message"]
    done = [i["id"] for i in _done_items(raw, "message")]
    assert added == done, f"added {added} vs done {done}"


# --- #376: oversized deferred chunk keeps its tail --------------------------

def test_responses_defer_keeps_tail_of_oversized_chunk():
    """One chunk larger than the cap must be trimmed to its newest tail, not
    dropped whole."""
    cap = orsp.MAX_DEFERRED_CHARS
    enc = orsp.ResponsesStreamEncoder("m", "R")
    enc._defer("text", "Z" * (cap + 5000))
    assert enc._deferred_chars == cap, (
        f"buffer should land exactly at the cap, got {enc._deferred_chars}")
    retained = "".join(t for _, t in enc._deferred)
    assert len(retained) == cap
    assert retained == "Z" * cap


def test_responses_defer_oversized_chunk_does_not_empty_the_buffer():
    """#376 as recorded: the whole buffer was emptied, discarding prior content
    that was nowhere near the cap."""
    cap = orsp.MAX_DEFERRED_CHARS
    enc = orsp.ResponsesStreamEncoder("m", "R")
    enc._defer("text", "KEEP")
    enc._defer("text", "Y" * (cap + 10))
    assert enc._deferred, "oversized chunk emptied the entire buffer"
    assert enc._deferred_chars == cap
    joined = "".join(t for _, t in enc._deferred)
    assert joined == "Y" * cap, "newest content must win inside the cap"


def test_responses_defer_still_evicts_from_the_head_within_the_cap():
    """Normal trimming: the oldest bytes go first and the newest are retained.

    The boundary entry keeps its TAIL, because the tail is the newest part of
    it and there is room for it — that is the same overshoot rule that keeps an
    oversized delta from being dropped whole.
    """
    cap = orsp.MAX_DEFERRED_CHARS
    enc = orsp.ResponsesStreamEncoder("m", "R")
    enc._defer("text", "A" * cap)
    enc._defer("text", "B")
    assert enc._deferred_chars == cap
    joined = "".join(t for _, t in enc._deferred)
    assert joined == "A" * (cap - 1) + "B", "newest content must be retained"
    assert not joined.startswith("A" * cap), "oldest byte should be trimmed"


def test_responses_defer_never_exceeds_cap():
    """Invariant across a stream of assorted chunk sizes."""
    cap = orsp.MAX_DEFERRED_CHARS
    enc = orsp.ResponsesStreamEncoder("m", "R")
    for n in (10, cap // 2, cap + 1, 3, cap * 2):
        enc._defer("text", "x" * n)
        assert enc._deferred_chars <= cap, f"cap exceeded at n={n}"
        assert enc._deferred_chars == sum(len(t) for _, t in enc._deferred)


def test_responses_defer_oversized_chunk_is_still_flushed():
    """End to end: the retained tail must reach the client."""
    cap = orsp.MAX_DEFERRED_CHARS
    enc = orsp.ResponsesStreamEncoder("m", "R")
    out = b""
    for d in [dl.StreamStart(model="m"),
              dl.ToolCallOpen(index=0, id="c1", name="fn"),
              dl.TextDelta(text="Z" * (cap + 5000)),
              dl.ToolCallClose(index=0)]:
        out += enc.feed(d) or b""
    out += enc._completed()
    items = _done_items(out, "message")
    assert items, "nothing flushed"
    assert items[0]["content"][0]["text"] == "Z" * cap


# --- #398: thinking tokens on the Anthropic streaming usage -----------------

def test_anthropic_stream_usage_reports_thinking_tokens():
    """The streamed message_delta usage must carry the same
    output_tokens_details.thinking_tokens the sync body does."""
    usage = _anthropic_stream_usage([
        dl.StreamStart(model="claude"), dl.TextDelta(text="hi"),
        dl.UsageFinal(prompt=10, output=20, reasoning=15),
        dl.Finish(stop_reason="stop"), dl.StreamEnd()])
    assert usage["output_tokens_details"]["thinking_tokens"] == 15


def test_anthropic_stream_and_sync_usage_agree():
    """Parity is the actual requirement — the two paths must not disagree."""
    turn = ir.AssistantTurn(text="hi", usage=ir.Usage(
        prompt_tokens=10, completion_tokens=20, reasoning_tokens=15))
    sync = am.encode_response(None, turn, "claude", "R")

    stream = _anthropic_stream_usage([
        dl.StreamStart(model="claude"), dl.TextDelta(text="hi"),
        dl.UsageFinal(prompt=10, output=20, reasoning=15),
        dl.Finish(stop_reason="stop"), dl.StreamEnd()])

    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens", "output_tokens_details"):
        assert stream[field] == sync["usage"][field], (
            f"{field}: stream={stream.get(field)!r} "
            f"sync={sync['usage'].get(field)!r}")


def test_anthropic_stream_usage_omits_thinking_details_at_zero():
    """Mirror the sync path's `if u.reasoning_tokens else {}` — a zero must
    not become a phantom ``thinking_tokens: 0``."""
    usage = _anthropic_stream_usage([
        dl.StreamStart(model="claude"), dl.TextDelta(text="hi"),
        dl.UsageFinal(prompt=10, output=20),
        dl.Finish(stop_reason="stop"), dl.StreamEnd()])
    assert usage["output_tokens_details"] == {}


def test_anthropic_stream_usage_start_frame_unchanged():
    """message_start keeps its prompt/cache fields (AUDIT #156)."""
    enc = am.AnthropicStreamEncoder("claude", "R")
    raw = enc.feed(dl.StreamStart(model="claude", prompt=11, cached=7,
                                  cache_creation=3))
    start = _events(raw)[0]
    u = start["message"]["usage"]
    assert (u["input_tokens"], u["cache_read_input_tokens"],
            u["cache_creation_input_tokens"]) == (11, 7, 3)
