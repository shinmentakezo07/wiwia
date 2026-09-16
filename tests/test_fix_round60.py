"""Regression tests for the NIM 400 on OpenAI-2026 platform params (AUDIT #152).

Codex CLI sends ``prompt_cache_key`` (and possibly ``safety_identifier``,
``store``, ``web_search_options``, ``verbosity``, ...) on the Responses
surface. The Responses codec keeps unmapped params in ``req.extras`` and the
OpenAI adapter forwards the OpenAI-2026 standard set upstream by default
(``_STANDARD`` in ``openai_adapter.py``). NIM is a vLLM-backed endpoint that
strict-validates params and rejects every one of those platform-only keys:

``{"message":"Validation: Unsupported parameter(s): `prompt_cache_key`",
"type":"Bad Request","code":400}``

The NIM adapter must strip the OpenAI-2026 platform params the same way it
already strips OpenAI reasoning fields.
"""
from __future__ import annotations

from wiwi.ir import types as ir
from wiwi.providers.nim_adapter import NimAdapter

_NIM_PARAMS = {"provider_type": "nvidia-nim"}


def _req(**extras) -> ir.Request:
    return ir.Request(
        model="nvidia/nemotron",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(),
        extras=dict(extras),
    )


def test_nim_strips_openai_2026_platform_params():
    """Every OpenAI-2026 platform param in _STANDARD must not reach NIM."""
    adapter = NimAdapter()
    body = adapter.encode_request(_req(
        prompt_cache_key="ck-1",
        safety_identifier="si-1",
        store=True,
        verbosity="low",
        web_search_options={"search_context_size": "medium"},
        prediction={"type": "content", "content": "hi"},
        modalities=["text"],
        audio={"voice": "alloy", "format": "wav"},
        logit_bias={"50256": -100},
        service_tier="auto",
    ), "nvidia/nemotron", dict(_NIM_PARAMS))
    for key in ("prompt_cache_key", "safety_identifier", "store", "verbosity",
                "web_search_options", "prediction", "modalities", "audio",
                "logit_bias", "service_tier"):
        assert key not in body, key


def test_nim_strips_prompt_cache_key_under_drop_params_false():
    """Stripping is capability-driven, independent of drop_params=False.

    drop_params=False means "forward unknown client params raw"; the 2026
    platform params are not unknown — they are known NIM-unsupported keys,
    so they must be dropped even when drop_params is disabled. Mirrors the
    reasoning-key stripping which also ignores drop_params.
    """
    body = NimAdapter().encode_request(_req(prompt_cache_key="ck-1"),
                                       "nvidia/nemotron",
                                       {"provider_type": "nvidia-nim",
                                        "drop_params": False})
    assert "prompt_cache_key" not in body


def test_nim_strips_platform_params_from_deployment_extra_body():
    """Platform params smuggled via deployment extra_body are stripped too.

    The OpenAI adapter merges extra_body top-level via body.setdefault, so a
    deployment extra_body {"prompt_cache_key": ...} lands at the top level —
    the same vector test_reasoning_keys_stripped_from_deployment_extra_body
    covers for reasoning keys.
    """
    body = NimAdapter().encode_request(_req(), "nvidia/nemotron",
                                       {"provider_type": "nvidia-nim",
                                        "extra_body": {
                                            "prompt_cache_key": "ck-1",
                                            "metadata": {"x": "1"},
                                        }})
    assert "prompt_cache_key" not in body


def test_nim_keeps_benign_params():
    """Params NIM actually supports must survive the strip."""
    req = _req(frequency_penalty=0.5, presence_penalty=0.5, user="u-1")
    req.gen_params.seed = 7
    body = NimAdapter().encode_request(req, "nvidia/nemotron",
                                       dict(_NIM_PARAMS))
    assert body.get("frequency_penalty") == 0.5
    assert body.get("presence_penalty") == 0.5
    assert body.get("user") == "u-1"
    assert body.get("seed") == 7


def test_openai_adapter_still_forwards_2026_params():
    """Control: the plain OpenAI adapter must keep forwarding these."""
    from wiwi.providers.openai_adapter import OpenAIAdapter

    body = OpenAIAdapter().encode_request(_req(prompt_cache_key="ck-1",
                                               safety_identifier="si-1"),
                                          "gpt-5",
                                          {"provider_type": "openai"})
    assert body.get("prompt_cache_key") == "ck-1"
    assert body.get("safety_identifier") == "si-1"
