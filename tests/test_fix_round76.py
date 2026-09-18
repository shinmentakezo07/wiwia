"""Round 76 — Anthropic Messages codec defects (AUDIT #178, #167, #168, #186, #187).

Each test defends one observable contract that was broken. The streaming tests
assert against the emitted SSE frames (what Claude Code / the Anthropic SDK
actually parses), never against encoder internals; the decode tests assert
against the body the Anthropic adapter puts on the wire.
"""

from __future__ import annotations

import json

from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am


def _frames(blob: bytes) -> list[dict]:
    """Every SSE frame's data payload, in order."""
    out = []
    for raw in blob.split(b"\n\n"):
        if not raw.strip():
            continue
        for line in raw.split(b"\n"):
            if line.startswith(b"data: "):
                out.append(json.loads(line[6:].decode()))
    return out


def _render(deltas) -> list[dict]:
    enc = am.AnthropicStreamEncoder("claude-x", "req-1")
    blob = b""
    for d in deltas:
        chunk = enc.feed(d)
        if chunk:
            blob += chunk
    return _frames(blob + enc.final_frame())


def _block_pairs(frames: list[dict]) -> tuple[list[int], list[int]]:
    starts = [f["index"] for f in frames if f.get("type") == "content_block_start"]
    stops = [f["index"] for f in frames if f.get("type") == "content_block_stop"]
    return starts, stops


def _assert_blocks_balanced(frames: list[dict]) -> None:
    """Every started block is stopped exactly once, and nothing else is."""
    starts, stops = _block_pairs(frames)
    assert starts == sorted(starts), f"block indices not monotonic: {starts}"
    assert sorted(starts) == sorted(stops), (
        f"unbalanced content blocks: starts={starts} stops={stops}")
    assert len(stops) == len(set(stops)), f"duplicate content_block_stop: {stops}"


def _upstream(req) -> dict:
    return AnthropicAdapter().encode_request(req, "claude-x", {})


# -- #178: the deferred block must be closed before the server call opens ----


def test_server_call_closes_deferred_text_block_first():
    """A client tool block is open and text was deferred while it was; the
    provider-hosted call that follows must drain and CLOSE the deferred text
    block before opening its own, or block 1 never stops and block 2 stops
    twice (AUDIT #178)."""
    frames = _render([
        dl.StreamStart("claude-x"),
        dl.ToolCallOpen(1, "toolu_grep", "Grep"),
        dl.TextDelta("Searching now."),
        dl.ToolCallOpen(3, "srvtoolu_1", "web_search",
                        builtin="web_search", block_type="server_tool_use"),
        dl.ToolCallArgsDelta(3, '{"query": "x"}'),
        dl.ToolCallClose(3),
        dl.ServerToolResultDelta(
            index=3, block={"type": "web_search_tool_result",
                            "tool_use_id": "srvtoolu_1", "content": []},
            builtin="web_search"),
        dl.UsageFinal(prompt=10, output=5),
        dl.Finish("stop"),
        dl.StreamEnd(),
    ])
    _assert_blocks_balanced(frames)
    # The exact frame sequence the audit recorded, with the defects removed:
    # 0 tool_use, 1 text (opened AND stopped), 2 server_tool_use, 3 result.
    assert [(f["type"], f["index"]) for f in frames
            if f["type"] in ("content_block_start", "content_block_stop")] == [
        ("content_block_start", 0), ("content_block_stop", 0),
        ("content_block_start", 1), ("content_block_stop", 1),
        ("content_block_start", 2), ("content_block_stop", 2),
        ("content_block_start", 3), ("content_block_stop", 3),
    ]
    kinds = {f["index"]: f["content_block"]["type"] for f in frames
             if f["type"] == "content_block_start"}
    assert kinds == {0: "tool_use", 1: "text", 2: "server_tool_use",
                     3: "web_search_tool_result"}
    # The model's prose survives rather than being lost with the block.
    assert [f["delta"]["text"] for f in frames
            if f.get("type") == "content_block_delta"
            and f["delta"]["type"] == "text_delta"] == ["Searching now."]
    # A1: the pair is still emitted together, never as a client tool_use.
    assert kinds[2] == "server_tool_use"


def test_server_call_does_not_stamp_signature_on_the_result_block():
    """With deferred THINKING instead of text, the signature used to be
    emitted against the server call's index — i.e. onto the result block
    (AUDIT #178). It belongs to the thinking block."""
    frames = _render([
        dl.StreamStart("claude-x"),
        dl.ToolCallOpen(0, "toolu_grep", "Grep"),
        dl.ThinkingDelta("pondering", signature="sig-abc"),
        dl.ToolCallOpen(1, "srvtoolu_1", "web_search",
                        builtin="web_search", block_type="server_tool_use"),
        dl.ToolCallClose(1),
        dl.ServerToolResultDelta(
            index=1, block={"type": "web_search_tool_result",
                            "tool_use_id": "srvtoolu_1", "content": []},
            builtin="web_search"),
        dl.Finish("stop"),
        dl.StreamEnd(),
    ])
    _assert_blocks_balanced(frames)
    kinds = {f["index"]: f["content_block"]["type"] for f in frames
             if f["type"] == "content_block_start"}
    assert kinds[1] == "thinking"
    assert kinds[2] == "server_tool_use"
    assert kinds[3] == "web_search_tool_result"
    sigs = [(f["index"], f["delta"]["signature"]) for f in frames
            if f.get("type") == "content_block_delta"
            and f["delta"]["type"] == "signature_delta"]
    assert sigs == [(1, "sig-abc")], (
        f"signature stamped on the wrong block: {sigs} (blocks: {kinds})")


def test_result_without_a_buffered_call_closes_deferred_block():
    """The same drain feeds the result block's own start/stop: an upstream
    result whose call was never buffered must not inherit an open text block's
    index (AUDIT #178)."""
    frames = _render([
        dl.StreamStart("claude-x"),
        dl.ToolCallOpen(0, "toolu_grep", "Grep"),
        dl.TextDelta("Searching now."),
        dl.ServerToolResultDelta(
            index=1, block={"type": "web_search_tool_result",
                            "tool_use_id": "srvtoolu_unknown", "content": []},
            builtin="web_search"),
        dl.Finish("stop"),
        dl.StreamEnd(),
    ])
    _assert_blocks_balanced(frames)
    kinds = {f["index"]: f["content_block"]["type"] for f in frames
             if f["type"] == "content_block_start"}
    assert kinds == {0: "tool_use", 1: "text", 2: "web_search_tool_result"}
    assert [f["delta"]["text"] for f in frames
            if f.get("type") == "content_block_delta"
            and f["delta"]["type"] == "text_delta"] == ["Searching now."]


# -- #167: an assistant turn with `content: null` is a real turn -------------


def test_null_content_assistant_turn_is_preserved():
    """``content: null`` is what the API emits for a tool-use-only turn;
    dropping it collapsed two user turns into one (AUDIT #167)."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "assistant", "content": None},
                     {"role": "user", "content": "hi"}]})
    assert [m.role for m in req.messages] == ["assistant", "user"]
    # The turn reaches an upstream as a real assistant turn (Chat's behaviour).
    assert [m["role"] for m in
            OpenAIAdapter().encode_request(req, "gpt-x", {})["messages"]] == [
        "assistant", "user"]


def test_null_content_assistant_turn_keeps_the_following_tool_turn():
    """A null-content assistant turn ahead of a tool_use turn must not swallow
    either of them: the replayed history stays alternating."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": None},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "f", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        ]})
    assert [m.role for m in req.messages] == [
        "user", "assistant", "assistant", "user"]


# -- #168: a malformed image block is dropped, not forwarded as null ---------


def test_base64_image_without_data_is_dropped():
    """``source.type == "base64"`` with no payload used to reach the upstream
    as ``"data": null`` — a 400 naming no offending block (AUDIT #168)."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64"}},
            {"type": "text", "text": "describe"}]}]})
    body = json.dumps(_upstream(req))
    assert '"data": null' not in body and '"data":null' not in body
    assert '"type": "image"' not in body
    assert [p.text for p in req.messages[0].parts] == ["describe"]


def test_url_and_file_images_without_a_reference_are_dropped():
    """A url/file source with no url/file_id has no source at all, and the
    adapters re-rendered it as a base64 image with ``data: null``."""
    for source in ({"type": "url"}, {"type": "file"}, {"type": "url", "url": ""},
                   {"type": "file", "file_id": None}):
        req = am.decode_request({
            "model": "claude-x", "max_tokens": 10,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": source},
                {"type": "text", "text": "describe"}]}]})
        body = json.dumps(_upstream(req))
        assert '"type": "image"' not in body, source
        assert '"data": null' not in body, source


def test_tool_result_image_without_data_is_dropped():
    """The tool_result arm has the same malformed-image path."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "image", "source": {"type": "base64"}}]}]}]})
    tr = req.messages[0].parts[0]
    assert tr.images == []
    assert '"data": null' not in json.dumps(_upstream(req))


def test_a_well_formed_image_still_round_trips():
    """The drop is scoped to junk: a real base64 image is untouched."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg",
                                         "data": "aGk="}}]}]})
    assert req.messages[0].parts[0].b64 == "aGk="
    assert req.messages[0].parts[0].mime == "image/jpeg"
    assert _upstream(req)["messages"][0]["content"][0]["source"] == {
        "type": "base64", "media_type": "image/jpeg", "data": "aGk="}


# -- #186: explicit JSON null for name/description --------------------------


def test_null_tool_use_name_is_coerced():
    """``b.get("name", "")`` defaults only a MISSING key, so ``name: null``
    reached the upstream as null (AUDIT #186)."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": None, "input": {}}]}]})
    part = req.messages[0].parts[0]
    assert part.name == ""
    assert _upstream(req)["messages"][0]["content"][0]["name"] == ""


def test_null_tool_definition_name_and_description_are_coerced():
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": None, "description": None,
                   "input_schema": {"type": "object"}}]})
    assert req.tools[0].name == "" and req.tools[0].description == ""
    tool = _upstream(req)["tools"][0]
    assert tool["name"] == "" and tool["description"] == ""


def test_null_named_tool_choice_is_coerced():
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d",
                   "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": None}})
    assert _upstream(req)["tool_choice"] == {"type": "tool", "name": ""}


# -- #187: typed-wrong media_type -------------------------------------------


def test_null_media_type_falls_back_to_png():
    """An explicit ``"media_type": null`` passed the dict.get default and was
    forwarded verbatim (AUDIT #187)."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": "aGk=",
                                         "media_type": None}}]}]})
    assert req.messages[0].parts[0].mime == "image/png"
    assert _upstream(req)["messages"][0]["content"][0]["source"] == {
        "type": "base64", "media_type": "image/png", "data": "aGk="}


def test_non_string_media_type_falls_back():
    """A list/number media_type is not a MIME type; it must not be forwarded
    (the Gemini/OpenAI adapters interpolate it into a data URL)."""
    for mime in ([1], 7, {"a": 1}):
        req = am.decode_request({
            "model": "claude-x", "max_tokens": 10,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "data": "aGk=",
                                             "media_type": mime}}]}]})
        assert req.messages[0].parts[0].mime == "image/png"


def test_null_document_media_type_falls_back_to_pdf():
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64",
                                            "data": "JVBERi0=",
                                            "media_type": None}}]}]})
    assert req.messages[0].parts[0].mime == "application/pdf"
