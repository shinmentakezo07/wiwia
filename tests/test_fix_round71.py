"""Round-71 regression tests: OpenCode Zen header scoping + free-tier policy.

Live probes 2026-09-17 changed the picture from round 29:

- Anonymous (keyless) free-tier traffic is now rejected upstream on every
  route with ``403 FreeTierError`` ("OpenCode's free tier can only be used
  from within OpenCode") — chat (``mimo-v2.5-free``), responses
  (``muse-spark-1.3-contributor-free``), messages (``union-alpha``).
- The same requests with a (fake) bearer get past the session gate to
  ``401 AuthError``, proving the ``x-opencode-*`` spoof still passes — the
  403 is an anonymous-traffic policy, not a missing/wrong spoof header.
- Free models therefore require a valid ``OPENCODE_API_KEY``; ``anonymous``
  no longer serves them.

These tests pin:

- ``anthropic-version`` rides only the messages route (it names Anthropic's
  API version; sending it to Responses/Chat/Gemini endpoints is meaningless)
- the responses route for ``muse-spark-1.3-contributor-free`` still carries
  the full client fingerprint + Authorization and encodes a well-formed
  Responses body
- Responses usage (incl. reasoning tokens) decodes into ``ir.Usage``
- a 403/401 policy refusal (``FreeTierError``) is classified as
  ``permission_error``, not ``authentication_error``, and does not poison the
  key pool — the regression behind the reported ``io rejected credentials
  (403)`` outage
"""

from __future__ import annotations

import time

import orjson

from wiwi.ir import types as ir
from wiwi.providers import opencode_version as ov
from wiwi.providers.base import (
    ProviderKeyRef,
    error_from_provider_status,
    status_for_key_pool,
)
from wiwi.providers.opencode_adapter import (
    OpencodeAdapter,
    _decode_responses_response,
    _encode_responses_request,
    route_for_model,
)
from wiwi.wire import openai_chat as oc


def _chat_req(text: str = "hi") -> ir.Request:
    body = {"model": "m", "messages": [{"role": "user", "content": text}]}
    return oc.decode_request(body)


def _key(secret: str = "zen-key-123") -> ProviderKeyRef:
    return ProviderKeyRef(label="main", secret=secret)


def setup_function(_func) -> None:
    ov._set_cached_for_tests("1.18.31", time.monotonic())


def teardown_function(_func) -> None:
    ov._set_cached_for_tests(None, 0.0)


def test_muse_spark_routes_to_responses() -> None:
    assert route_for_model("muse-spark-1.3-contributor-free") == "responses"


def test_anthropic_version_only_on_messages_route() -> None:
    for model, want in [
        ("muse-spark-1.3-contributor-free", False),  # responses
        ("gpt-5.5", False),  # responses
        ("mimo-v2.5-free", False),  # chat
        ("glm-5.3-flash", False),  # chat
        ("gemini-3-flash", False),  # gemini
        ("union-alpha", True),  # messages
        ("claude-sonnet-5", True),  # messages
    ]:
        a = OpencodeAdapter()
        a.encode_request(_chat_req(), model, {})
        a.build_url("https://opencode.ai/zen/v1", model, False)
        h = a.headers(_key())
        assert ("anthropic-version" in h) is want, model
        # The client fingerprint rides every route regardless.
        assert h["x-opencode-session"].startswith("ses_"), model
        assert h["x-opencode-client"] == "cli", model
        assert h["User-Agent"] == "opencode/1.18.31", model


def test_muse_spark_responses_body_shape() -> None:
    body = _encode_responses_request(
        _chat_req("hello"), "muse-spark-1.3-contributor-free", {})
    assert body["model"] == "muse-spark-1.3-contributor-free"
    assert body["stream"] is False
    assert body["input"] == [{
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "hello"}],
    }]


def test_muse_spark_responses_usage_decode() -> None:
    raw = orjson.dumps({
        "status": "completed",
        "output": [{"type": "message",
                    "content": [{"type": "output_text", "text": "hi"}]}],
        "usage": {"input_tokens": 7, "output_tokens": 3,
                  "input_tokens_details": {"cached_tokens": 2},
                  "output_tokens_details": {"reasoning_tokens": 5}},
    })
    turn = _decode_responses_response(raw)
    assert turn.text == "hi"
    assert turn.stop_reason == "stop"
    assert turn.usage.prompt_tokens == 7
    assert turn.usage.completion_tokens == 3
    assert turn.usage.cached_tokens == 2
    assert turn.usage.reasoning_tokens == 5


# -- entitlement 403s must not poison the key pool ------------------------------

_ZEN_FREE_TIER_403 = (
    '{"type":"error","error":{"type":"FreeTierError",'
    '"message":"Error from provider (Console): OpenCode\'s free tier can only '
    'be used from within OpenCode"}}'
)


def test_zen_free_tier_403_is_permission_not_auth() -> None:
    # A free-tier refusal is a billing condition: report 402 so a client does
    # not render it as "re-enter your API key", and keep the non-auth etype.
    err = error_from_provider_status(403, _ZEN_FREE_TIER_403, "io")
    assert err.status == 402
    assert err.etype == "permission_error"
    assert err.retryable is True
    # The reported wording ("rejected credentials") implied a bad key; a policy
    # refusal must not read as a credential rejection.
    assert "rejected credentials" not in err.message
    assert "requires billing" in err.message


def test_zen_free_tier_403_leaves_key_pool_untouched() -> None:
    # Pre-fix this returned 403, so the router ran key.err_count += 2 and
    # retired the key after a few policy rejections — the log showed io/3 and
    # io/4 marked rejected by a request that never reached an auth check.
    err = error_from_provider_status(403, _ZEN_FREE_TIER_403, "io")
    assert status_for_key_pool(err) is None


def test_real_credential_401_still_marks_key_unhealthy() -> None:
    # A genuine bad-key body must keep the historical semantics.
    err = error_from_provider_status(
        401, '{"type":"error","error":{"type":"AuthError","message":"Invalid API key."}}',
        "io")
    assert err.etype == "authentication_error"
    assert err.status == 401
    assert status_for_key_pool(err) == 401


def test_billing_401_markers_become_402_permission() -> None:
    # Zen returns these as 401 with a machine type; all are account-level.
    for body in [
        '{"type":"error","error":{"type":"CreditsError","message":"No payment method."}}',
        '{"type":"error","error":{"type":"MonthlyLimitError","message":"workspace limit"}}',
        '{"type":"error","error":{"type":"UserLimitError","message":"member limit"}}',
    ]:
        err = error_from_provider_status(401, body, "io")
        assert err.status == 402, body
        assert err.etype == "permission_error", body
        assert status_for_key_pool(err) is None, body


def test_policy_401_markers_stay_403_permission() -> None:
    for body in [
        '{"type":"error","error":{"type":"RegionError","message":"not available in your country"}}',
        '{"type":"error","error":{"type":"ModelError","message":"Model x is not supported"}}',
    ]:
        err = error_from_provider_status(401, body, "io")
        assert err.status == 403, body
        assert err.etype == "permission_error", body
        assert status_for_key_pool(err) is None, body


def test_entitlement_phrase_fallback_without_type_marker() -> None:
    # Some Console variants emit the policy text without a machine type.
    err = error_from_provider_status(
        403,
        '{"error":{"message":"OpenCode\'s free tier can only be used from '
        'within OpenCode"}}',
        "io")
    assert err.status == 402
    assert err.etype == "permission_error"
    assert status_for_key_pool(err) is None


def test_plain_403_still_auth_classified() -> None:
    # A bare 403 with no policy marker stays a credential rejection.
    err = error_from_provider_status(403, '{"error":{"message":"forbidden"}}', "io")
    assert err.etype == "authentication_error"
    assert err.status == 403
    assert status_for_key_pool(err) == 403
