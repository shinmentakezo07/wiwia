"""Round 91 — the healer must not call a 200 error body HEALTHY.

AUDIT #269. ``probe_verdict(status, body)`` recognized only WorkBuddy's
``{"code": <non-zero>, "msg": …}`` envelope, so an Anthropic- or OpenAI-shaped
upstream answering HTTP 200 with its own error object was declared HEALTHY —
and the healer's job is to *restore* a credential on a successful probe, so it
re-armed the very failure it exists to clear. Those bodies are invisible one
layer down: ``decode_response(200, <error envelope>)`` returns an empty but
*successful* turn with no exception and no signal.

Scope note: this file originally also asserted a second, larger defect — that
``_probe_request()``'s hardcoded ``model="wiwi-health-probe"`` reached the wire
and caused 404/401 misclassification. **That was false** and is retracted in
AUDIT #269. No adapter reads ``ir.Request.model`` for the wire body; every one
sets it from ``encode_request``'s own ``model_id`` argument, which the pre-fix
call site already passed ``dep.model_id`` to. The class below now pins that rule
down instead of asserting the retracted claim.
"""
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from wiwi.config import HealerSettings
from wiwi.core.recovery import HealthHealer, ProbeVerdict, probe_verdict
from wiwi.providers.registry import fresh_adapter


def _healer(**overrides) -> HealthHealer:
    """A healer with a permissive router double: these tests never sweep."""
    settings = HealerSettings(enabled=True, **overrides)
    return HealthHealer(router=MagicMock(), settings=settings)


def _deployment(provider_type: str, model_id: str, base_url: str):
    """Minimal deployment double carrying only what ``_probe`` reads."""
    provider = MagicMock()
    provider.provider_type = provider_type
    provider.name = "probe-provider"
    provider.base_url = base_url
    provider.extra_headers = {}

    dep = MagicMock()
    dep.provider = provider
    dep.model_id = model_id
    dep.group = "probe-group"
    dep.extra_headers = {}
    dep.cooldown_until = 0.0
    return dep


def _key():
    key = MagicMock()
    key.label = "probe-key"
    key.secret = "sk-secret"
    return key


PROBE_URL = "https://upstream.test/v1/chat/completions"


class TestProbeRequestCarriesTheDeploymentModel:
    """The probe's request and its encode call must agree on the model id.

    These deliberately pass a *different* value to each half: a placeholder to
    ``_probe_request`` and the deployment's id to ``encode_request``. A test
    that passed the same value to both would hold even if ``_probe_request``
    ignored its argument completely — it could only ever detect the signature
    change, never the behaviour, and failing on a ``TypeError`` would read as
    RED evidence for a defect that isn't there. That mistake was made once in
    this round; this is the version that cannot make it again.
    """

    @pytest.mark.parametrize(
        "provider_type",
        ["openai", "anthropic", "openrouter", "bai", "gmicloud",
         "nvidia-nim", "cline", "workbuddy", "opencode"],
    )
    def test_encode_request_uses_its_own_argument_not_the_request_field(
            self, provider_type):
        """The wire model comes from ``encode_request``'s argument.

        Documents the rule the healer depends on: ``ir.Request.model`` is not
        the wire model for any adapter in ``PROVIDER_TYPES``, so the two must
        not be expected to match. If an adapter ever starts reading
        ``req.model``, this fails and the healer's call site needs revisiting.
        """
        from wiwi.core.recovery import _probe_request

        adapter = fresh_adapter(provider_type)
        stream = bool(getattr(adapter, "force_stream", False))
        params: dict[str, Any] = {"max_tokens": 1, "extra_body": {},
                                 "drop_params": True,
                                 "provider_type": provider_type}
        # A placeholder the function is given, and a DIFFERENT id the encoder
        # is given. The wire must carry the encoder's.
        body = adapter.encode_request(
            _probe_request(stream, "placeholder-should-not-win"),
            "deployment-model-id", params)

        assert body.get("model") == "deployment-model-id", (
            f"{provider_type}: the probe body carries {body.get('model')!r} — "
            "an adapter has started reading ir.Request.model, so the healer's "
            "request field and its encode argument are no longer independent")

    @respx.mock
    async def test_probe_posts_the_deployment_model_to_the_upstream(self):
        """End-to-end through ``_probe``: what actually goes on the wire."""
        route = respx.post(PROBE_URL).mock(return_value=httpx.Response(
            200, json={"id": "c1", "object": "chat.completion", "choices": [
                {"index": 0, "message": {"role": "assistant",
                                         "content": "p"},
                 "finish_reason": "stop"}]}))

        healer = _healer()
        dep = _deployment("openai", "gpt-4o-mini", "https://upstream.test/v1")
        try:
            verdict, _detail, _ra = await healer._probe(dep, _key())
        finally:
            await healer.stop()

        assert verdict is ProbeVerdict.HEALTHY
        sent = json.loads(route.calls[0].request.content)
        assert sent["model"] == "gpt-4o-mini"


class TestAnthropicAndOpenAI200ErrorEnvelopesAreNotHealthy:
    """AUDIT #269 (second half) — the 200-envelope check must not be one shape.

    ``probe_verdict`` recognizes WorkBuddy's ``{"code": N, "msg": …}`` envelope
    and nothing else. Anthropic and OpenAI ride business errors on HTTP 200 as
    their own dialect-shaped error objects, which decode into an empty turn with
    no exception — so the probe called a dead key healthy and restored it.
    """

    ANTHROPIC_ERROR = b'{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'
    ANTHROPIC_OVERLOADED = b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'
    OPENAI_ERROR = b'{"error":{"message":"Incorrect API key provided","type":"invalid_request_error","code":"invalid_api_key"}}'
    OPENAI_QUOTA = b'{"error":{"message":"You exceeded your current quota","type":"insufficient_quota"}}'

    @pytest.mark.parametrize("body", [
        ANTHROPIC_ERROR, ANTHROPIC_OVERLOADED, OPENAI_ERROR, OPENAI_QUOTA,
    ])
    def test_a_200_error_body_is_not_healthy(self, body):
        assert probe_verdict(200, body) is not ProbeVerdict.HEALTHY, (
            f"a 200 carrying {body[:48]!r} was declared HEALTHY, so the healer "
            f"would restore a key that cannot serve traffic")

    ANTHROPIC_OK = (
        b'{"id":"msg_1","type":"message","role":"assistant","model":"m",'
        b'"content":[{"type":"text","text":"p"}],"stop_reason":"end_turn",'
        b'"usage":{"input_tokens":1,"output_tokens":1}}')
    OPENAI_OK = (
        b'{"id":"c1","object":"chat.completion","choices":[{"index":0,'
        b'"message":{"role":"assistant","content":"p"},'
        b'"finish_reason":"stop"}]}')

    @pytest.mark.parametrize("provider_type,body", [
        ("anthropic", ANTHROPIC_OK),
        ("openai", OPENAI_OK),
    ])
    def test_a_genuine_200_completion_is_still_healthy(self, provider_type, body):
        assert probe_verdict(200, body) is ProbeVerdict.HEALTHY, (
            f"a real {provider_type} completion was rejected as an error body")

    def test_a_bare_200_with_no_body_stays_healthy(self):
        # The pre-existing contract: an empty body proves nothing either way,
        # and force-stream providers legitimately return one.
        assert probe_verdict(200) is ProbeVerdict.HEALTHY
        assert probe_verdict(200, None) is ProbeVerdict.HEALTHY
        assert probe_verdict(200, b"") is ProbeVerdict.HEALTHY

    # Force-stream providers (cline, workbuddy, opencode) answer the probe with
    # SSE even on HTTP 200, so the error object arrives inside a ``data:`` frame
    # rather than as a bare JSON body. That arm is a *separate* branch in
    # ``_body_is_error_envelope`` — it has its own parse loop — and the four
    # cases above never reach it. Without these the SSE arm could regress
    # silently while the bare-JSON suite stayed green.
    SSE_ANTHROPIC_ERROR = (
        b'event: error\n'
        b'data: {"type":"error","error":{"type":"overloaded_error",'
        b'"message":"Overloaded"}}\n\n')
    SSE_OPENAI_ERROR = (
        b'data: {"error":{"message":"Incorrect API key provided",'
        b'"type":"invalid_request_error"}}\n\n')

    @pytest.mark.parametrize("body", [SSE_ANTHROPIC_ERROR, SSE_OPENAI_ERROR])
    def test_a_200_sse_error_frame_is_not_healthy(self, body):
        assert probe_verdict(200, body) is not ProbeVerdict.HEALTHY, (
            f"a force-stream 200 carrying {body[:40]!r} inside an SSE frame was "
            f"declared HEALTHY")

    SSE_ANTHROPIC_OK = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"msg_1"}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    def test_a_200_sse_completion_is_still_healthy(self):
        assert probe_verdict(200, self.SSE_ANTHROPIC_OK) is ProbeVerdict.HEALTHY
