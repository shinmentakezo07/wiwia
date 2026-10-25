"""Round-174 regressions: AUDIT #359 — the structured-output schema rides the
IR unvalidated.

``ResponseFormat.json_schema`` is typed ``dict | None`` but is a plain
dataclass with no runtime enforcement. All three inbound decoders accepted any
client value verbatim (the Chat codec guarded only the *wrapper*, not the
inner ``schema``), so a junk ``"schema": "oops"`` crossed the IR and re-encoded
upstream — Anthropic's native ``output_config.format.schema``, OpenAI's
``response_format.json_schema.schema``, Gemini's ``responseSchema`` — a 400
naming no field the caller controls. Each decoder now coerces a non-dict
schema to the always-legal ``{"type": "object"}`` and non-string ``name`` to
``None``, matching the ``parameters_json_schema`` convention the tool decoders
already use.
"""

from __future__ import annotations

from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as oresp

JUNK = ["oops", "", 5, 2.5, True, None, ["a"], None]


def test_anthropic_output_config_schema_coerced_to_object():
    # native carrier: output_config.format.schema
    body = {
        "model": "m", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"format": {"type": "json_schema", "name": "n",
                                     "schema": "oops"}},
    }
    r = am.decode_request(body)
    fmt = r.gen_params.response_format
    assert fmt is not None and fmt.type == "json_schema"
    assert fmt.json_schema == {"type": "object"}


def test_anthropic_output_config_junk_name_dropped():
    body = {
        "model": "m", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"format": {"type": "json_schema",
                                     "name": 7, "schema": {"type": "object"}}},
    }
    r = am.decode_request(body)
    fmt = r.gen_params.response_format
    assert fmt is not None and fmt.name is None


def test_anthropic_valid_schema_passes_through():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    body = {
        "model": "m", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"format": {"type": "json_schema", "name": "n",
                                     "schema": schema}},
    }
    r = am.decode_request(body)
    assert r.gen_params.response_format.json_schema == schema


def test_responses_schema_coerced_to_object():
    r = oresp.decode_request({
        "model": "m", "input": "hi",
        "text": {"format": {"type": "json_schema", "name": "n",
                            "schema": "oops"}},
    })
    fmt = r.gen_params.response_format
    assert fmt is not None and fmt.type == "json_schema"
    assert fmt.json_schema == {"type": "object"}


def test_responses_junk_name_dropped():
    r = oresp.decode_request({
        "model": "m", "input": "hi",
        "schema": {"type": "object", "properties": {}},
        "text": {"format": {"type": "json_schema", "name": False,
                            "schema": {"type": "object"}}},
    })
    assert r.gen_params.response_format.name is None


def test_responses_valid_schema_passes_through():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    r = oresp.decode_request({
        "model": "m", "input": "hi",
        "text": {"format": {"type": "json_schema", "name": "n",
                            "schema": schema}},
    })
    assert r.gen_params.response_format.json_schema == schema


def test_chat_inner_schema_coerced_to_object():
    # Chat's wrapper guard exists (AUDIT #100); the INNER schema was unguarded.
    r = oc.decode_request({
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "n", "schema": "oops"}},
    })
    fmt = r.gen_params.response_format
    assert fmt is not None and fmt.type == "json_schema"


def test_chat_json_object_keeps_schema_none():
    # The coercion must not leak into json_object, whose wire shape has no
    # json_schema member — the adapter would emit one and the API 400.
    r = oc.decode_request({
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_object"},
    })
    assert r.gen_params.response_format.json_schema is None


def test_chat_junk_name_dropped():
    r = re_chat_fmt(name={"x": 1})
    assert r.gen_params.response_format.name is None




def test_chat_valid_schema_passes_through():
    schema = {"type": "object", "properties": {"a": {"type": "boolean"}}}
    r = re_chat_fmt(schema=schema)
    assert r.gen_params.response_format.json_schema == schema


def re_chat_fmt(schema=None, name="n"):
    js: dict = {}
    if schema is not None:
        js["schema"] = schema
    if name is not None:
        js["name"] = name
    return oc.decode_request({
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_schema", "json_schema": js},
    })


def test_junk_schema_does_not_reach_anthropic_upstream():
    """End to end: a junk schema must not cross the IR into output_config."""
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    body = {
        "model": "m", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"format": {"type": "json_schema", "name": "n",
                                     "schema": "oops"}},
    }
    req = am.decode_request(body)
    encoded = AnthropicAdapter().encode_request(req, "claude-x", {})
    fmt = encoded["output_config"]["format"]
    assert fmt["schema"] == {"type": "object"}


def test_junk_values_do_not_crash_any_decoder():
    for v in JUNK:
        am.decode_request({
            "model": "m", "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
            "output_config": {"format": {"type": "json_schema",
                                         "name": v, "schema": v}},
        })
        oresp.decode_request({
            "model": "m", "input": "hi",
            "text": {"format": {"type": "json_schema", "name": v, "schema": v}},
        })
        oc.decode_request({
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": v, "schema": v}},
        })
