"""Round 151: Zen refuses the caller's client-metadata block.

Reported 2026-10-15 against a free model:

    400 io: Upstream request failed: [unsupported_metadata] Client metadata is
    not supported.

Read as data rather than as a diagnosis (docs/UPSTREAM_DIAGNOSIS.md, step 0),
that string has two layers:

* ``Upstream request failed: [<code>] <message>`` is **Console's own** wrapper,
  and the bracketed code is the *upstream's*: Console drops codes outside its
  allow-list and keeps the original as a ``[code]`` label. opencode's client
  pins the format in ``packages/ai/test/provider-error.test.ts`` — "Zen drops
  upstream codes outside its allow-list and replaces the type, but
  ``Protocol.errorMessage`` keeps the original code as a ``[code]`` prefix".
* So ``unsupported_metadata`` / "Client metadata is not supported." comes from
  the provider behind Zen, relayed. wiwi renders it as ``<account>: <message>``
  (``base.py:_classify_provider_status``'s 400 branch), which is where the
  ``io:`` prefix in the report comes from.

What the provider refuses is a *client metadata* block, and the only one this
adapter can put on the wire is the caller's ``metadata`` field: both inbound
codecs capture a top-level ``metadata`` into ``req.extras`` and both delegates
forward it (``openai_adapter._STANDARD``, ``anthropic_adapter.
_ANTHROPIC_STANDARD``). Zen's own client sends none on these wires — its request
builder attaches ``metadata`` only on the Responses protocol, and only when
provider options set it — so the upstream never sees the field for a real client
and sees it for us.

Same failure class in two other proxies: openclaw #73963 ("injects unsupported
metadata field into openai-codex Responses requests; ChatGPT backend returns
HTTP 400"), CLIProxyAPI #5112 ("Codex Responses: strip unsupported top-level
metadata"). The fix there, as here, is to stop forwarding the field.

Pinned below: the drop on every route that can carry it (free *and* paid, since
the refusal is a provider fact rather than a tier gate), the ``metadata: null``
case, the ``extra_body`` escape hatch for an operator who wants it anyway, the
log line, and — the boundary — that a non-Zen provider still forwards it.

Measured context for what is *not* the cause (live probes, 2026-10-15, all
recorded in docs/OPENCODE.md): the client fingerprint was re-derived from the
client's own source at commit ``4397ee4`` and still matches — one ``ses_`` + 12
hex + 14 base62 id on both session headers, the v2 tool pair, no
``x-opencode-request``. A sweep of all twelve current free models with the
adapter's exact shape answers 200 (the deprecated two answer 410), and every
fingerprint drift was probed one factor at a time: the 64-hex session id and a
``prj_``-style project id 403 as the round-149 matrix said, and a body
``metadata`` / ``store`` / ``prompt_cache_key`` changes nothing on the anonymous
path.
"""

from __future__ import annotations

import structlog

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.opencode_adapter import OpencodeAdapter
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

CHAT = "space-bunny-free"
PAID_CHAT = "glm-5.3"
MESSAGES = "qwen3.8-flash"
RESPONSES = "muse-spark-1.3-contributor-free"
DROP_EVENT = "opencode_client_metadata_dropped"


def _chat_req(**extra) -> ir.Request:
    body: dict = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    body.update(extra)
    return oc.decode_request(body)


def _anthropic_req(**extra) -> ir.Request:
    body: dict = {"model": "m", "max_tokens": 64,
                  "messages": [{"role": "user", "content": "hi"}]}
    body.update(extra)
    return am.decode_request(body)


# -- the drop ----------------------------------------------------------------


def test_the_caller_metadata_never_reaches_zen_on_the_chat_route():
    req = _chat_req(metadata={"user_id": "u1"})
    assert req.extras["metadata"] == {"user_id": "u1"}, "the wire still captures it"
    body = OpencodeAdapter().encode_request(req, CHAT, {})
    assert "metadata" not in body


def test_the_caller_metadata_never_reaches_zen_on_the_messages_route():
    # The Anthropic delegate forwards it under _ANTHROPIC_STANDARD, and Zen's
    # /messages front end is one of the four routes that answers the same 400.
    req = _anthropic_req(metadata={"user_id": "u1"})
    body = OpencodeAdapter().encode_request(req, MESSAGES, {})
    assert "metadata" not in body


def test_the_drop_is_provider_wide_not_free_tier_only():
    # The refusal is an upstream-provider fact, not a tier gate, so a paid
    # deployment must not send the field either.
    for model in (PAID_CHAT, MESSAGES, CHAT):
        req = _chat_req(metadata={"user_id": "u1"})
        body = OpencodeAdapter().encode_request(req, model, {})
        assert "metadata" not in body, model


def test_a_null_metadata_is_dropped_too():
    # A strict upstream can refuse ``"metadata": null`` just as it refuses a
    # populated object, and the extras capture keeps the key either way.
    body = OpencodeAdapter().encode_request(_chat_req(metadata=None), CHAT, {})
    assert "metadata" not in body


def test_the_responses_route_never_carried_it():
    # That encoder builds its body field by field and forwards no extras, so the
    # drop is a no-op there — pinned so a future extras pass-through does not
    # silently re-open the hole on the one route the free tier still serves.
    req = _chat_req(metadata={"user_id": "u1"})
    body = OpencodeAdapter().encode_request(req, RESPONSES, {})
    assert "metadata" not in body


def test_only_metadata_is_dropped_the_other_extras_still_ride():
    # The fix must not become a general extras purge: Zen accepts these (probed
    # 2026-10-15) and the caller asked for them.
    req = _chat_req(metadata={"user_id": "u1"}, store=True,
                    prompt_cache_key="cache-me", safety_identifier="user-123")
    body = OpencodeAdapter().encode_request(req, CHAT, {})
    assert "metadata" not in body
    assert body["store"] is True
    assert body["prompt_cache_key"] == "cache-me"
    assert body["safety_identifier"] == "user-123"


# -- the log line and the escape hatch ---------------------------------------


def test_the_drop_is_logged_with_the_route():
    with structlog.testing.capture_logs() as logs:
        OpencodeAdapter().encode_request(_chat_req(metadata={"user_id": "u1"}), CHAT, {})
    drops = [entry for entry in logs if entry["event"] == DROP_EVENT]
    assert len(drops) == 1
    assert drops[0]["route"] == "chat"


def test_a_request_without_metadata_logs_nothing():
    with structlog.testing.capture_logs() as logs:
        OpencodeAdapter().encode_request(_chat_req(), CHAT, {})
    assert [entry for entry in logs if entry["event"] == DROP_EVENT] == []


def test_an_operator_can_put_the_field_back_with_extra_body():
    # The drop targets the *caller's* telemetry, not the operator's explicit
    # configuration: a deployment that injects metadata itself keeps it, and
    # wins over the caller's copy (extra_body is applied before extras).
    params = {"extra_body": {"metadata": {"operator": "yes"}}}
    req = _chat_req(metadata={"user_id": "u1"})
    body = OpencodeAdapter().encode_request(req, CHAT, params)
    assert body["metadata"] == {"operator": "yes"}


def test_extra_body_works_even_with_no_caller_metadata():
    params = {"extra_body": {"metadata": {"operator": "yes"}}}
    body = OpencodeAdapter().encode_request(_chat_req(), CHAT, params)
    assert body["metadata"] == {"operator": "yes"}


# -- the boundary: nothing else changes --------------------------------------


def test_a_non_zen_provider_still_forwards_the_callers_metadata():
    # The field is a documented passthrough for the vendors that support it
    # (tests/test_translation_enhancements.py pins the OpenAI half); only Zen's
    # upstreams refuse it, so the drop must stay inside this adapter.
    req = _chat_req(metadata={"session_id": "s1"})
    native = OpenAIAdapter().encode_request(req, "gpt-5.2", {})
    assert native["metadata"] == {"session_id": "s1"}
    anth = AnthropicAdapter().encode_request(
        _anthropic_req(metadata={"user_id": "u1"}), "claude-sonnet-4-5", {})
    assert anth["metadata"] == {"user_id": "u1"}
