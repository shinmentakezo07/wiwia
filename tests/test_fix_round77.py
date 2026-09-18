"""Round 77: decode-boundary coercions in the OpenAI Chat and Responses codecs.

Seven findings from the round-68/75 sweeps share one root cause — a field typed
``str``/``list[str]``/``int`` on the IR is populated from caller- or
provider-controlled JSON with no type check, so junk rides through the codec
and surfaces somewhere far from its origin:

* **#165** ``_builtin_query`` called ``.get`` on whatever ``orjson.loads``
  returned, so a hosted ``web_search`` call whose upstream streamed ``[1]``,
  ``5`` or ``"abc"`` as its arguments raised ``AttributeError`` mid-stream —
  after content had already been sent.
* **#166** a non-string ``role`` was forwarded upstream verbatim (the
  ``# type: ignore[arg-type]`` on the append was the tell).
* **#168** ``_decode_image`` returned an ``ImagePart`` for an absent or empty
  ``image_url``, so a malformed block survived as a 4-byte "image"
  (``data:image/png;base64,None``) instead of being dropped.
* **#184** a non-string ``text``/tool ``name`` reached the IR typed as ``str``
  and crashed ``flatten_request_text``'s ``" ".join`` — an
  ``internal gateway error`` 500 on the success path, after the upstream had
  been billed.
* **#185** a truthy non-list ``stop`` (``true``, ``7``, ``{"a": 1}``) passed the
  ``or []`` guard and was forwarded upstream verbatim.
* **#186** an explicit JSON ``null`` for ``name``/``description`` passed
  ``fn.get("name", "")`` — the default covers only a MISSING key.
* **#187** ``openai_responses`` read ``top_k`` with no ``coerce_int``, unlike
  ``max_output_tokens`` on the line above.

Every test asserts the decoded IR or the re-encoded outbound body, never codec
internals.
"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import flatten_request_text
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orr

JUNK = [5, True, None, 1.5, ["a"], {"a": 1}]

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}

CHAT_SSE = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"hi"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


@pytest.fixture
async def client():
    """The real app on the ASGI transport, one OpenAI deployment behind it."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://api.openai.com/v1",
                               keys=[KeyDef(label="a", key="sk-test")])],
        model_list=[ModelEntry(model_name="gpt-5",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-5"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"))
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


def _sse_events(blob: str) -> list[dict]:
    out = []
    for line in blob.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return out


def _chat(**over: object) -> dict:
    body: dict = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    body.update(over)
    return body


def _resp(**over: object) -> dict:
    body: dict = {"model": "m", "input": [{"type": "message", "role": "user",
                                           "content": [{"type": "input_text",
                                                        "text": "hi"}]}]}
    body.update(over)
    return body


def _openai_body(req: ir.Request) -> dict:
    return OpenAIAdapter().encode_request(req, "m", {})


def _anthropic_body(req: ir.Request) -> dict:
    return AnthropicAdapter().encode_request(req, "m", {})


def _flat(req: ir.Request, surface: str = "responses") -> str:
    return flatten_request_text(RequestContext(surface=surface, ir_req=req))


def _str_fields(obj: object, path: str = "ir") -> list[str]:
    """Every ``str``-annotated IR field whose value is not a ``str``.

    A cheap annotation-aware walk over the decoded request, so a test can
    assert the IR contract itself rather than one hand-picked field. Returns
    ``[]`` when the decode boundary held.
    """
    out: list[str] = []
    seen: set[int] = set()

    def walk(node: object, where: str) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                value = getattr(node, f.name)
                ann = f.type if isinstance(f.type, str) else str(f.type)
                if ann == "str" and not isinstance(value, str):
                    out.append(f"{where}.{f.name} = {value!r}")
                walk(value, f"{where}.{f.name}")
        elif isinstance(node, (list, tuple)):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{where}[{key!r}]")

    walk(obj, path)
    return out


# ---------------------------------------------------------------------------
# #165 — a hosted web_search call whose args are not a JSON object
# ---------------------------------------------------------------------------

def _builtin_stream_close(args_fragment: str) -> list[dict]:
    """Feed one hosted web_search call through the Responses stream encoder.

    The hosted call's query is read at ``ToolCallClose`` (``_close_tool`` →
    ``_builtin_query``), so the close is what exercises the guard — the
    terminal event is not needed to reach it.
    """
    enc = orr.ResponsesStreamEncoder("m", "r1")
    blob = b""
    for d in [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="ws_1", name="web_search", builtin="web_search"),
        dl.ToolCallArgsDelta(index=0, args_fragment=args_fragment),
        dl.ToolCallClose(index=0),
    ]:
        out = enc.feed(d)
        if out:
            blob += out
    return _sse_events(blob.decode())


@pytest.mark.parametrize("fragment", ["[1]", "5", '"abc"', "null", "true", "1.5"])
def test_responses_builtin_call_with_non_object_args_does_not_crash(fragment):
    """A well-formed JSON scalar/array parses fine but has no ``.get``.

    Pre-fix this raised ``AttributeError`` from inside the stream encoder —
    mid-stream, after content had already been sent to the client.
    """
    events = _builtin_stream_close(fragment)
    items = [e["item"] for e in events
             if e.get("type") == "response.output_item.done"
             and e["item"].get("type") == "web_search_call"]
    assert items, "the hosted call must still close with its own item"
    assert items[0]["action"]["query"] == ""


def test_responses_builtin_call_query_still_extracted_from_an_object():
    """The guard must not blank a real query — only non-objects lose one."""
    events = _builtin_stream_close('{"query": "cats"}')
    items = [e["item"] for e in events
             if e.get("type") == "response.output_item.done"
             and e["item"].get("type") == "web_search_call"]
    assert items[0]["action"]["query"] == "cats"


# ---------------------------------------------------------------------------
# #166 — non-string role forwarded upstream verbatim
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", [7, True, None, ["user"], {"a": 1}, 1.5])
def test_chat_non_string_role_decodes_to_user(role):
    req = oc.decode_request(_chat(messages=[{"role": role, "content": "hi"}]))
    assert req.messages[0].role == "user"


@pytest.mark.parametrize("role", [7, None, ["user"], {"a": 1}])
def test_chat_non_string_role_never_reaches_the_upstream(role):
    """The re-encoded outbound body must carry a real role, not the junk."""
    req = oc.decode_request(_chat(messages=[{"role": role, "content": "hi"}]))
    msgs = _openai_body(req)["messages"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hi"


def test_chat_valid_roles_are_untouched():
    """The coercion must not disturb the roles the dialect does define."""
    req = oc.decode_request(_chat(messages=[
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "u"},
        {"role": "developer", "content": "d"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
    ]))
    assert [m.role for m in req.messages] == [
        "system", "assistant", "user", "system", "assistant", "tool"]


# ---------------------------------------------------------------------------
# #168 — a malformed image block forwarded as a bogus image
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("block", [
    {"type": "input_image"},
    {"type": "input_image", "image_url": ""},
    {"type": "input_image", "image_url": None},
    {"type": "image", "image_url": ""},
])
def test_responses_empty_image_url_is_dropped(block):
    req = orr.decode_request(_resp(input=[{
        "type": "message", "role": "user",
        "content": [block, {"type": "input_text", "text": "kept"}]}]))
    parts = req.messages[0].parts
    assert not any(isinstance(p, ir.ImagePart) for p in parts)
    assert [p.text for p in parts if isinstance(p, ir.TextPart)] == ["kept"]


def test_responses_empty_image_url_does_not_emit_a_bogus_data_url():
    """The re-encoded outbound body must not contain a base64 ``None`` image."""
    req = orr.decode_request(_resp(input=[{
        "type": "message", "role": "user",
        "content": [{"type": "input_image"}, {"type": "input_text", "text": "kept"}]}]))
    encoded = json.dumps(_openai_body(req))
    assert "base64,None" not in encoded
    assert "image_url" not in encoded


@pytest.mark.parametrize("url", ["", None])
def test_responses_tool_result_images_skip_empty_urls(url):
    """``_item_images`` shares ``_decode_image``: same guard, same drop."""
    req = orr.decode_request(_resp(input=[{
        "type": "function_call_output", "call_id": "c1", "output": [
            {"type": "input_image", "image_url": url},
            {"type": "output_text", "text": "shot"}]}]))
    assert req.messages[0].parts[0].images == []


def test_responses_real_image_url_still_decodes():
    """The guard must not swallow a genuine image reference."""
    req = orr.decode_request(_resp(input=[{
        "type": "message", "role": "user",
        "content": [{"type": "input_image", "image_url": "https://x/y.png"}]}]))
    img = req.messages[0].parts[0]
    assert isinstance(img, ir.ImagePart) and img.url == "https://x/y.png"


# ---------------------------------------------------------------------------
# #184 — non-string text / tool name reaches the IR typed as str
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", JUNK)
def test_responses_non_string_text_decodes_to_empty_string(text):
    req = orr.decode_request(_resp(input=[{
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": text}]}]))
    assert req.messages[0].parts[0].text == ""


@pytest.mark.parametrize("ctype", ["input_text", "output_text", "text"])
@pytest.mark.parametrize("text", JUNK)
def test_responses_all_text_block_types_are_coerced(ctype, text):
    role = "assistant" if ctype == "output_text" else "user"
    req = orr.decode_request(_resp(input=[{
        "type": "message", "role": role, "content": [{"type": ctype, "text": text}]}]))
    assert req.messages[0].parts[0].text == ""


@pytest.mark.parametrize("text", JUNK)
def test_chat_non_string_text_block_stays_coerced(text):
    req = oc.decode_request(_chat(messages=[{"role": "user", "content": [
        {"type": "text", "text": text}]}]))
    assert req.messages[0].parts[0].text == ""


@pytest.mark.parametrize("name", JUNK)
def test_responses_non_string_function_call_name_decodes_to_empty(name):
    req = orr.decode_request(_resp(input=[{
        "type": "function_call", "call_id": "c1", "name": name,
        "arguments": "{}"}]))
    assert req.messages[0].parts[0].name == ""


@pytest.mark.parametrize("name", JUNK)
def test_chat_non_string_tool_call_name_decodes_to_empty(name):
    req = oc.decode_request(_chat(messages=[{"role": "assistant", "tool_calls": [
        {"id": "c1", "function": {"name": name, "arguments": "{}"}}]}]))
    assert req.messages[0].parts[0].name == ""


@pytest.mark.parametrize("value", JUNK)
def test_responses_non_string_tool_name_and_description(value):
    req = orr.decode_request(_resp(tools=[{"type": "function", "name": value,
                                           "description": value,
                                           "parameters": {"type": "object"}}]))
    assert req.tools[0].name == ""
    assert req.tools[0].description == ""


@pytest.mark.parametrize("value", JUNK)
def test_chat_non_string_tool_name_and_description(value):
    req = oc.decode_request(_chat(tools=[{"type": "function", "function": {
        "name": value, "description": value, "parameters": {"type": "object"}}}]))
    assert req.tools[0].name == ""
    assert req.tools[0].description == ""


def test_responses_non_string_tool_choice_name_decodes_to_empty():
    """``ToolChoiceNamed.name`` is typed ``str`` and every adapter renders it."""
    req = orr.decode_request(_resp(tool_choice={"type": "function", "name": 5}))
    assert req.tool_choice == ir.ToolChoiceNamed("")


def test_chat_non_string_tool_choice_name_decodes_to_empty():
    req = oc.decode_request(_chat(tool_choice={"type": "function",
                                               "function": {"name": 5}}))
    assert req.tool_choice == ir.ToolChoiceNamed("")


@pytest.mark.parametrize("value", [5, True, ["a"], 1.5])
def test_responses_non_string_function_call_arguments(value):
    """``raw_args`` is typed ``str | None`` and is joined by the estimator."""
    req = orr.decode_request(_resp(input=[{
        "type": "function_call", "call_id": "c1", "name": "f",
        "arguments": value}]))
    part = req.messages[0].parts[0]
    assert part.raw_args == "{}"
    assert part.args == {}


def test_flatten_request_text_survives_a_non_string_text_on_responses():
    """The end-to-end consequence of #184.

    ``flatten_request_text`` is the streaming fallback estimator's input. A
    non-string ``text`` reached the IR typed as ``str`` and its ``" ".join``
    raised ``TypeError: sequence item 0: expected str instance, int found`` —
    an ``internal gateway error`` 500 *after* the upstream had been billed.
    The junk must also contribute nothing to the estimate: with the decode-site
    coercion it never becomes text at all, rather than being stringified into
    a phantom token.
    """
    req = orr.decode_request(_resp(input=[{
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": 5},
                    {"type": "input_text", "text": "kept"}]}]))
    flat = _flat(req)
    assert isinstance(flat, str)
    assert flat.split() == ["kept"]


def test_flatten_request_text_survives_every_junk_typed_field():
    """One sweep over the fields #184 names, on both codecs.

    Asserts the decoded IR itself — every ``str``-annotated field is a ``str``
    — and then that the estimator consumes it. The IR assertion is what fails
    pre-fix: ``flatten_request_text`` now coerces at its join as a backstop, so
    a crash-only assertion would be masked by that.
    """
    for value in JUNK:
        req = orr.decode_request(_resp(
            instructions=value,
            input=[
                {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": value},
                    {"type": "input_file",
                     "file_data": "data:application/pdf;base64,AA",
                     "filename": value}]},
                {"type": "function_call", "call_id": "c1", "name": value,
                 "arguments": "{}"},
            ],
            tools=[{"type": "function", "name": value, "description": value,
                    "parameters": {"type": "object"}},
                   {"type": "file_search", "name": value}]))
        assert _str_fields(req) == [], f"responses junk={value!r}"
        _flat(req)

        chat_req = oc.decode_request(_chat(
            messages=[
                {"role": "user", "content": [{"type": "text", "text": value}]},
                {"role": "assistant", "tool_calls": [
                    {"id": "c1", "function": {"name": value, "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "r"},
            ],
            tools=[{"type": "function", "function": {
                "name": value, "description": value,
                "parameters": {"type": "object"}}}]))
        assert _str_fields(chat_req) == [], f"chat junk={value!r}"
        _flat(chat_req, surface="chat")


@respx.mock
async def test_responses_request_with_non_string_text_reaches_upstream_as_text(client):
    """The end-to-end consequence of #184, through the real ``/v1/responses``
    route.

    Pre-fix the decoded ``TextPart.text`` was the integer, so the re-encoded
    Chat body handed the upstream ``"content": 5`` — and the streaming
    fallback estimator's ``" ".join`` raised on the same value. Both are
    observable here: the upstream body must carry text, and the request must
    complete with 200 rather than an ``internal gateway error``.
    """
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        text=CHAT_SSE, headers={"content-type": "text/event-stream"})
    r = await client.post("/v1/responses", json={
        "model": "gpt-5", "stream": True,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": 5},
            {"type": "input_text", "text": "kept"}]}],
    }, headers=AUTH)
    assert r.status_code == 200, r.text
    sent = json.loads(route.calls.last.request.content)
    content = sent["messages"][0]["content"]
    assert isinstance(content, str), f"upstream got {content!r}"
    assert content == "kept"


# ---------------------------------------------------------------------------
# #185 — a non-list scalar `stop` forwarded upstream verbatim
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [True, 7, {"a": 1}, 1.5])
def test_non_list_stop_decodes_to_an_empty_list(value):
    assert oc.decode_request(_chat(stop=value)).gen_params.stop == []
    assert orr.decode_request(_resp(stop=value)).gen_params.stop == []


@pytest.mark.parametrize("value", [True, 7, {"a": 1}])
def test_non_list_stop_never_reaches_the_upstream(value):
    for req in (oc.decode_request(_chat(stop=value)),
                orr.decode_request(_resp(stop=value))):
        assert "stop" not in _openai_body(req)
        assert "stop_sequences" not in _anthropic_body(req)


def test_string_and_list_stop_still_decode():
    """The guard must not break the two shapes the dialect defines."""
    for req in (oc.decode_request(_chat(stop="END")),
                orr.decode_request(_resp(stop="END"))):
        assert req.gen_params.stop == ["END"]
    for req in (oc.decode_request(_chat(stop=["a", "b"])),
                orr.decode_request(_resp(stop=["a", "b"]))):
        assert req.gen_params.stop == ["a", "b"]


@pytest.mark.parametrize("value,expected", [
    ([1], []),
    (["END", 1], ["END"]),
    ([None, True, "END"], ["END"]),
    ([], []),
])
def test_non_string_stop_items_are_filtered(value, expected):
    """AUDIT #127's shape: a non-string item inside the list is dropped."""
    for req in (oc.decode_request(_chat(stop=value)),
                orr.decode_request(_resp(stop=value))):
        assert req.gen_params.stop == expected


# ---------------------------------------------------------------------------
# #186 — explicit JSON null for name/description
# ---------------------------------------------------------------------------

def test_responses_explicit_null_tool_name_and_description():
    req = orr.decode_request(_resp(tools=[{"type": "function", "name": None,
                                           "description": None,
                                           "parameters": {"type": "object"}}]))
    assert req.tools[0].name == ""
    assert req.tools[0].description == ""
    body = _openai_body(req)["tools"][0]
    assert body["function"]["name"] == ""
    assert body["function"]["description"] == ""


def test_chat_explicit_null_tool_name_and_description():
    req = oc.decode_request(_chat(tools=[{"type": "function", "function": {
        "name": None, "description": None, "parameters": {"type": "object"}}}]))
    assert req.tools[0].name == ""
    assert req.tools[0].description == ""
    assert _anthropic_body(req)["tools"][0]["name"] == ""


def test_explicit_null_function_call_name_is_not_forwarded():
    resp = orr.decode_request(_resp(input=[{
        "type": "function_call", "call_id": "c1", "name": None,
        "arguments": "{}"}]))
    assert resp.messages[0].parts[0].name == ""
    chat = oc.decode_request(_chat(messages=[{"role": "assistant", "tool_calls": [
        {"id": "c1", "function": {"name": None, "arguments": "{}"}}]}]))
    assert chat.messages[0].parts[0].name == ""
    encoded = json.dumps(_openai_body(chat))
    assert '"name": null' not in encoded


def test_explicit_null_unknown_builtin_name_falls_back_to_the_wire_type():
    """The unknown-hosted-tool arm's ``t.get("name") or ttype`` must still
    yield a ``str``: a null name falls back to the wire type, and a non-string
    *type* (which the guard above turns into ``None``) must not leave ``None``
    in a ``str`` field."""
    req = orr.decode_request(_resp(tools=[{"type": "file_search", "name": None}]))
    assert req.tools[0].name == "file_search"
    for value in (5, True, {"a": 1}, ["a"]):
        req = orr.decode_request(_resp(tools=[{"type": value, "name": None}]))
        assert isinstance(req.tools[0].name, str)


# ---------------------------------------------------------------------------
# #187 — top_k read with no coerce_int
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["7", True, {"a": 1}, [7], "abc", None])
def test_responses_non_int_top_k_decodes_to_none(value):
    assert orr.decode_request(_resp(top_k=value)).gen_params.top_k is None


def test_responses_int_top_k_is_kept_and_forwarded():
    req = orr.decode_request(_resp(top_k=7))
    assert req.gen_params.top_k == 7
    assert _anthropic_body(req)["top_k"] == 7


def test_responses_float_top_k_is_truncated_like_max_output_tokens():
    """``coerce_int`` accepts an integral float — the same rule as its sibling."""
    assert orr.decode_request(_resp(top_k=7.0)).gen_params.top_k == 7
    assert orr.decode_request(_resp(top_k=7.5)).gen_params.top_k is None


@pytest.mark.parametrize("value", ["7", True, {"a": 1}])
def test_responses_non_int_top_k_never_reaches_the_upstream(value):
    req = orr.decode_request(_resp(top_k=value))
    assert "top_k" not in _anthropic_body(req)
