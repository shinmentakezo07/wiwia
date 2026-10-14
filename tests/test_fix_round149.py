"""Round 149: the v2 client's fingerprint, derived from a live capture.

Round 88 (2026-09-19) derived the free tier's admission gate from a probe matrix
and got it right for the v1 client. The client has since moved to v2, and the
adapter kept speaking v1, so every `*-free` request answered
`403 FreeTierError` while the real client worked from the same host.

Round 147 misdiagnosed that as "the free tier is closed upstream" and made the
decoy cloak opt-in. **That conclusion was wrong**, and this round is the
correction. The gate never closed: the *client's* request shape changed, and
wiwi's had not. What made round 147's control invalid was that it ran the real
client from a directory whose `opencode.json` overrode the provider `baseURL`,
so the "genuine CLI fails too" result was an artefact of the override, not
evidence about the gate.

Method: captured the real client's outbound request verbatim (a logging proxy in
front of the provider, dumping headers and body), replayed it, then bisected one
factor at a time. Every row below is a measured result against the live
upstream, 2026-10-14.

| factor                                                | result |
|-------------------------------------------------------|--------|
| captured request, replayed                            | **200** |
| tool payload has no tools                             | 403 |
| tool payload has 5 real tools                         | 403 |
| tool payload has 6 real tools                         | **200** |
| `read` + `shell` with their real schemas              | **200** |
| `read` + `shell` with the description swapped         | **200** |
| `read` + `shell` with `parameters` emptied            | 403 |
| 6 inert decoys (any names)                            | 403 |
| 6 real tools renamed                                  | 403 |
| drop `x-opencode-session-id`                          | 403 |
| drop `Authorization` (the anonymous keyless path)     | 403 |
| add `HTTP-Referer`                                    | 403 |
| add `x-opencode-request`                              | 403 |
| drop `x-opencode-project` / `-client` / `Accept` / tracing | **200** |
| session id `ses_`+26-char vs `ses_`+64-hex            | 26-char **200**, 64-hex 403 |
| UA `opencode/1.19.0` / `2.0.26` / `latest/2.0.26/cli` | **200** |
| UA `opencode/1.16.0` / `1.17.0`                       | 426 UpgradeRequired |
| UA `opencode/unknown`                                 | 403 |

Four defects fell out, all of them "we were still speaking v1":

1. **The decoys were the v1 pair.** v1's second tool was `bash`; v2 renamed it
   `shell`. Worse, the gate reads the tools' *parameter schemas*, not just their
   names or their count — inert empty schemas 403 whatever they are called.
2. **`Authorization` was omitted for the `anonymous` sentinel.** The anonymous
   client sends `Bearer public`; the gate reads the header's *presence*. Round
   88's "keyless is the working path, no credential on the wire" was right about
   the value and wrong about the header.
3. **`x-opencode-session-id` was never sent.** v2 rides the session id on both
   headers and sends no `x-opencode-request` at all — which, when supplied,
   also 403s.
4. **`HTTP-Referer` was an invention** the client does not send, and it trips
   the gate.

The UA was already fine (`opencode/<version>` is accepted), but its **fallback**
was not: a cold cache sent `opencode/unknown`, which 403s — so the first seconds
after startup, and any registry outage, failed every free request. The fallback
is now a version the upstream still accepts.

Pinned below: the decoy names and schemas, `Bearer public` for the anonymous
sentinel, both session headers, the absence of `HTTP-Referer` and
`x-opencode-request`, the accepted UA floor, and the cloak staying on by default.
"""

from __future__ import annotations

import time

import pytest

from wiwi.ir import types as ir
from wiwi.providers import opencode_adapter as oa
from wiwi.providers import opencode_version as ov
from wiwi.providers.base import ProviderKeyRef
from wiwi.wire import openai_chat as oc

FREE_CHAT = "space-bunny-free"
FREE_RESPONSES = "muse-spark-1.3-contributor-free"
PAID_CHAT = "glm-5.1"


@pytest.fixture(autouse=True)
def _seed_ua():
    ov._set_cached_for_tests("1.19.0", time.monotonic())
    oa._reset_stable_sessions_for_tests()
    yield
    ov._set_cached_for_tests(None, 0.0)
    oa._reset_stable_sessions_for_tests()


def _req(*, tools: list[str] | None = None) -> ir.Request:
    body: dict = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": n, "description": "d",
            "parameters": {"type": "object", "properties": {}}}} for n in tools]
    return oc.decode_request(body)


def _headers(secret: str = "anonymous") -> dict[str, str]:
    a = oa.OpencodeAdapter()
    a.build_url("https://opencode.ai/inference/v1", FREE_CHAT, True)
    return a.headers(ProviderKeyRef(label="k", secret=secret))


# -- the tool payload the gate reads ------------------------------------------


def test_the_decoys_are_the_v2_pair_not_the_v1_one():
    body = oa.OpencodeAdapter().encode_request(_req(), FREE_CHAT, {})
    names = [t["function"]["name"] for t in body["tools"]]
    # v1 said ("bash", "read"); v2 renamed bash -> shell.
    assert names == ["read", "shell"]
    assert "bash" not in names


def test_the_decoys_carry_the_clients_real_parameter_schemas():
    # The gate reads the schemas: six inert decoys 403, and read+shell with
    # emptied parameters 403. Only the description may be substituted.
    body = oa.OpencodeAdapter().encode_request(_req(), FREE_CHAT, {})
    by_name = {t["function"]["name"]: t["function"] for t in body["tools"]}
    assert set(by_name["read"]["parameters"]["properties"]) == {"path", "offset", "limit"}
    assert by_name["read"]["parameters"]["required"] == ["path"]
    assert set(by_name["shell"]["parameters"]["properties"]) == {
        "command", "workdir", "timeout", "background"}
    assert by_name["shell"]["parameters"]["required"] == ["command"]
    # Descriptions are ours, so the model is told the tools are unavailable.
    for fn in by_name.values():
        assert "must not be used" in fn["description"]


def test_the_responses_route_gets_the_same_schemas_in_its_flat_shape():
    body = oa.OpencodeAdapter().encode_request(_req(), FREE_RESPONSES, {})
    by_name = {t["name"]: t for t in body["tools"]}
    assert {"read", "shell"} <= set(by_name)
    assert set(by_name["shell"]["parameters"]["properties"]) == {
        "command", "workdir", "timeout", "background"}
    assert all("function" not in t for t in body["tools"])


def test_a_client_tool_of_the_same_name_is_not_replaced():
    req = _req(tools=["shell", "get_weather"])
    body = oa.OpencodeAdapter().encode_request(req, FREE_CHAT, {})
    shells = [t for t in body["tools"] if t["function"]["name"] == "shell"]
    assert len(shells) == 1
    assert shells[0]["function"]["description"] == "d", "the client's own tool won"
    assert [t["function"]["name"] for t in body["tools"]] == [
        "shell", "get_weather", "read"]


# -- the headers ---------------------------------------------------------------


def test_the_anonymous_sentinel_sends_bearer_public():
    # Round 88 asserted no Authorization header at all. The gate reads its
    # presence: the anonymous client sends `Bearer public`.
    assert _headers()["Authorization"] == "Bearer public"
    assert oa.ANONYMOUS_BEARER == "Bearer public"


def test_a_real_key_still_uses_the_route_scheme():
    h = _headers("sk-a-real-key")
    assert h["Authorization"] == "Bearer sk-a-real-key"
    assert "x-api-key" not in h  # chat route reads the OpenAI-wire header


def test_the_session_id_rides_both_headers():
    h = _headers()
    assert oa.OPENCODE_SESSION_RE.match(h["x-opencode-session"])
    assert h["x-opencode-session-id"] == h["x-opencode-session"]


def test_the_client_sends_no_request_id_header_and_neither_do_we():
    # v2 sends none, and supplying one 403s.
    assert "x-opencode-request" not in _headers()
    assert not hasattr(oa, "OPENCODE_REQUEST_RE")
    assert not hasattr(oa, "canonical_request_id")


def test_no_referer_header_is_invented():
    # The client sends neither HTTP-Referer nor a request id; adding either
    # turns a 200 into a 403.
    h = _headers()
    assert "http-referer" not in {k.lower() for k in h}
    assert "x-title" in {k.lower() for k in h}, "referral attribution is harmless"


def test_the_session_is_reused_across_instances_of_one_credential():
    a = oa.OpencodeAdapter()
    b = oa.OpencodeAdapter()
    ka, kb = ProviderKeyRef(label="anon", secret="anonymous"), ProviderKeyRef(label="anon",
                                                                             secret="anonymous")
    assert a.headers(ka)["x-opencode-session"] == b.headers(kb)["x-opencode-session"]


# -- the User-Agent floor ------------------------------------------------------


def test_the_cold_cache_fallback_is_a_version_the_upstream_accepts():
    # `opencode/unknown` answers 403 on the free tier; below ~1.19 answers 426.
    ov._set_cached_for_tests(None, 0.0)
    assert ov.get_cached_version() == ov.FALLBACK_VERSION == "1.19.0"
    assert ov.build_user_agent() == "opencode/1.19.0"


def test_a_live_version_wins_over_the_fallback():
    ov._set_cached_for_tests("2.0.26", time.monotonic())
    assert ov.build_user_agent() == "opencode/2.0.26"


# -- the cloak stays on by default ---------------------------------------------


def test_the_cloak_is_on_unless_a_deployment_opts_out():
    assert oa._cloak_requested({}) is True
    assert oa._cloak_requested({oa.FREE_TIER_CLOAK_PARAM: True}) is True
    assert oa._cloak_requested({oa.FREE_TIER_CLOAK_PARAM: False}) is False


def test_paid_models_are_never_cloaked():
    for model in (PAID_CHAT, "gpt-5.5", "claude-sonnet-5"):
        body = oa.OpencodeAdapter().encode_request(_req(), model, {})
        assert "tools" not in body, model


def test_the_filter_drops_a_call_to_an_injected_decoy():
    from wiwi.streaming import deltas as dl
    a = oa.OpencodeAdapter()
    a.encode_request(_req(), FREE_CHAT, {})
    got = a._filter_decoys([dl.ToolCallOpen(index=0, id="c0", name="shell"),
                            dl.ToolCallClose(index=0), dl.Finish("tool_call")])
    # The client has no `shell`, so the call is dropped whole and the finish
    # reason follows the calls that actually survived.
    assert [type(x) for x in got] == [dl.Finish]
    assert got[0].stop_reason == "stop"
