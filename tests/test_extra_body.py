"""Tests for the extra_body deployment parameter: passing provider-specific
JSON fields (like OpenRouter's ``provider`` routing object) through the
config -> router -> gateway -> adapter pipeline.

Covers the minimax/minimax-m3 -> GMICloud routing use case.
"""

from textwrap import dedent

from wiwi.config import load_config
from wiwi.ir import types as ir
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.router.router import Router
from wiwi.wire import openai_chat as oc

# -- config: extra_body loaded from YAML -------------------------------------

def test_extra_body_loaded_from_yaml(tmp_path):
    """extra_body in wiwi.yaml is parsed into DeploymentParams.extra_body."""
    p = tmp_path / "wiwi.yaml"
    p.write_text(dedent("""
    providers:
      - name: openrouter
        provider: openrouter
        base_url: https://openrouter.ai/api/v1
        keys: [{label: main, key: sk-test}]
    model_list:
      - model_name: minimax/minimax-m3
        wiwi_params:
          provider: openrouter
          model: minimax/minimax-m3
          extra_body:
            provider:
              only: ["gmicloud"]
    general_settings:
      master_key: test
    """))
    cfg = load_config(p)
    entry = cfg.model_list[0]
    assert entry.wiwi_params.extra_body == {"provider": {"only": ["gmicloud"]}}


def test_extra_body_empty_by_default(tmp_path):
    """Models without extra_body get an empty dict, not None."""
    p = tmp_path / "wiwi.yaml"
    p.write_text(dedent("""
    providers:
      - name: openai-main
        provider: openai
        keys: [{label: main, key: sk-test}]
    model_list:
      - model_name: gpt-4o
        wiwi_params: {provider: openai-main, model: gpt-4o}
    general_settings:
      master_key: test
    """))
    cfg = load_config(p)
    assert cfg.model_list[0].wiwi_params.extra_body == {}


# -- router: extra_body threaded into Deployment -----------------------------

def test_deployment_carries_extra_body():
    """The router's Deployment object gets the extra_body from config."""
    from wiwi.config import (
        DeploymentParams,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )
    cfg = WiwiConfig(
        providers=[
            ProviderDef(name="or", provider="openrouter",
                        keys=[KeyDef(label="k", key="secret")]),
        ],
        model_list=[
            ModelEntry(
                model_name="minimax/minimax-m3",
                wiwi_params=DeploymentParams(
                    provider="or", model="minimax/minimax-m3",
                    extra_body={"provider": {"only": ["gmicloud"]}},
                ),
            ),
        ],
    )
    router = Router(cfg)
    dep = router.groups["minimax/minimax-m3"][0]
    assert dep.extra_body == {"provider": {"only": ["gmicloud"]}}


# -- adapter: extra_body merged into request body ---------------------------

def test_openai_adapter_merges_extra_body():
    """OpenAI adapter merges deployment_params['extra_body'] into the body."""
    req = ir.Request(
        model="minimax/minimax-m3",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
    )
    params = {"extra_body": {"provider": {"only": ["gmicloud"]}}}
    body = OpenAIAdapter().encode_request(req, "minimax/minimax-m3", params)
    assert body["provider"] == {"only": ["gmicloud"]}


def test_openrouter_adapter_merges_extra_body():
    """OpenRouter adapter (via OpenAI parent) merges extra_body into the body."""
    req = oc.decode_request({
        "model": "minimax/minimax-m3",
        "messages": [{"role": "user", "content": "hello"}],
    })
    params = {"extra_body": {"provider": {"only": ["gmicloud"]}},
              "provider_type": "openrouter"}
    body = OpenRouterAdapter().encode_request(req, "minimax/minimax-m3", params)
    assert body["provider"] == {"only": ["gmicloud"]}
    # Standard fields still present
    assert body["model"] == "minimax/minimax-m3"
    assert body["stream"] is False


def test_extra_body_does_not_override_client_params():
    """If the client already set a field, extra_body should not overwrite it
    (setdefault semantics)."""
    req = ir.Request(
        model="test-model",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(temperature=0.7),
    )
    # Client sets temperature=0.7; extra_body tries temperature=0.1
    params = {"extra_body": {"temperature": 0.1}}
    body = OpenAIAdapter().encode_request(req, "test-model", params)
    assert body["temperature"] == 0.7  # client wins


def test_openrouter_gmicloud_routing_end_to_end():
    """Full end-to-end: client request -> IR -> OpenRouter body with provider
    routing pinned to gmicloud."""
    req = oc.decode_request({
        "model": "minimax/minimax-m3",
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    })
    # Simulate what the gateway passes: dep.extra_body dict
    params = {
        "extra_body": {"provider": {"only": ["gmicloud"]}},
        "provider_type": "openrouter",
        "drop_params": True,
    }
    body = OpenRouterAdapter().encode_request(req, "minimax/minimax-m3", params)
    # The provider routing object must be present
    assert body["provider"] == {"only": ["gmicloud"]}
    # Model is the native OpenRouter model id
    assert body["model"] == "minimax/minimax-m3"
    # Stream options preserved (client explicitly requested usage)
    assert body["stream_options"] == {"include_usage": True}
    # No reasoning keys for a plain request
    assert "reasoning" not in body
    assert "reasoning_effort" not in body
